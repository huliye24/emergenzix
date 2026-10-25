"""Verified OHLCV -> causal, train-only-normalized supervised research samples."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from emergenzix_api.dataset_store import verify_stored_dataset


@dataclass(frozen=True)
class ResearchDataset:
    dataset_id: str
    symbol: str
    timeframe: str
    times: tuple[str, ...]
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    open: np.ndarray
    volume: np.ndarray


@dataclass(frozen=True)
class Samples:
    train_x: np.ndarray
    train_y: np.ndarray
    val_x: np.ndarray
    val_y: np.ndarray
    test_x: np.ndarray
    test_y: np.ndarray
    train_anchors: np.ndarray
    val_anchors: np.ndarray
    test_anchors: np.ndarray
    feature_mean: np.ndarray
    feature_std: np.ndarray
    train_cut: int
    val_cut: int


_TIMEFRAME_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400}


def load_registered_dataset(db_path: str | Path, dataset_id: str) -> ResearchDataset:
    """Read existing immutable market data; never create or modify SQLite."""
    if len(dataset_id) != 64 or any(ch not in "0123456789abcdef" for ch in dataset_id):
        raise ValueError("dataset_id must be 64 lowercase hex characters")
    path = Path(db_path).expanduser().resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
    if row is None:
        raise ValueError("registered dataset does not exist")
    bars = verify_stored_dataset(row)
    if row["timeframe"] not in _TIMEFRAME_SECONDS:
        raise ValueError("experiment 001 accepts only 1h, 4h or 1d data")
    fields = {}
    for key in ("open", "high", "low", "close", "volume"):
        fields[key] = np.asarray([bar[key] for bar in bars], dtype=np.float64)
        if not np.isfinite(fields[key]).all():
            raise ValueError("non-finite OHLCV value")
    if any(np.any(fields[key] <= 0) for key in ("open", "high", "low", "close")):
        raise ValueError("OHLC prices must be positive")
    if np.any(fields["volume"] < 0):
        raise ValueError("volume cannot be negative")
    times = tuple(str(bar["timestamp"]) for bar in bars)
    parsed = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times]
    if any(t.tzinfo is None or t.utcoffset().total_seconds() != 0 for t in parsed):
        raise ValueError("timestamps must have UTC timezone")
    expected = _TIMEFRAME_SECONDS[row["timeframe"]]
    if any((b - a).total_seconds() != expected for a, b in zip(parsed, parsed[1:])):
        raise ValueError("incomplete or non-monotonic historical candle sequence")
    if len(times) < 120:
        raise ValueError("experiment requires at least 120 candles")
    return ResearchDataset(
        dataset_id=dataset_id, symbol=row["symbol"], timeframe=row["timeframe"],
        times=times, **fields,
    )


def make_samples(
    dataset: ResearchDataset, *, lookback: int = 32, horizon: int = 1
) -> Samples:
    """Chronological purged folds and train-only normalization.

    Anchor t sees bars <= t; label uses close[t+horizon].
    Train: t+horizon < train_cut, validation: t >= train_cut and
    t+horizon < val_cut, test: t >= val_cut.
    """
    if not (8 <= lookback <= 256 and 1 <= horizon <= 24):
        raise ValueError("lookback must be 8..256 and horizon 1..24")
    n = len(dataset.close)
    train_cut, val_cut = int(n * 0.70), int(n * 0.85)
    if (train_cut - horizon - lookback < 32 or
        val_cut - train_cut <= horizon + 4 or
        n - val_cut <= horizon + 4):
        raise ValueError("insufficient history for purged train/val/test splits")

    close = dataset.close
    previous_close = np.r_[close[0], close[:-1]]
    previous_volume = np.r_[dataset.volume[0], dataset.volume[:-1]]
    features = np.column_stack((
        np.log(close / previous_close),
        np.log(dataset.high / dataset.low),
        np.log(close / dataset.open),
        np.log1p(dataset.volume) - np.log1p(previous_volume),
    ))
    if not np.isfinite(features).all():
        raise ValueError("non-finite derived financial features")
    last_train_anchor = train_cut - horizon - 1
    mean = features[:last_train_anchor + 1].mean(axis=0)
    std = np.maximum(features[:last_train_anchor + 1].std(axis=0), 1e-8)
    normalized = np.clip((features - mean) / std, -10.0, 10.0)

    groups: dict[str, tuple[list[np.ndarray], list[float], list[int]]] = {
        name: ([], [], []) for name in ("train", "val", "test")
    }
    for anchor in range(lookback - 1, n - horizon):
        if anchor + horizon < train_cut:
            name = "train"
        elif train_cut <= anchor and anchor + horizon < val_cut:
            name = "val"
        elif anchor >= val_cut:
            name = "test"
        else:
            continue  # purge boundary-crossing labels
        x, y, anchors = groups[name]
        x.append(normalized[anchor - lookback + 1:anchor + 1].astype(np.float32))
        y.append(float(np.log(close[anchor + horizon] / close[anchor])))
        anchors.append(anchor)

    if any(len(g[0]) < 5 for g in groups.values()):
        raise ValueError("each purged split must contain at least five examples")

    def arrays(name: str):
        x, y, anchors = groups[name]
        return (np.stack(x), np.asarray(y, dtype=np.float32),
                np.asarray(anchors, dtype=np.int64))

    tr_x, tr_y, tr_i = arrays("train")
    va_x, va_y, va_i = arrays("val")
    te_x, te_y, te_i = arrays("test")
    return Samples(tr_x, tr_y, va_x, va_y, te_x, te_y, tr_i, va_i, te_i,
                   mean, std, train_cut, val_cut)
