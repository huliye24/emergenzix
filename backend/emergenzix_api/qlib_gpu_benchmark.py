"""Causal dataset contract for the offline Qlib GPU benchmark.

The training runner is intentionally separate from the Emergenzix API.  This
module consumes only closed, immutable public candles and grants no strategy
promotion or order permission.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .chronos_walk_forward import SYMBOLS, TIMEFRAMES, chronological_origins


TRAIN_END = pd.Timestamp("2026-06-30 23:59:59")
VALID_START = pd.Timestamp("2026-07-01 00:00:00")
VALID_END = pd.Timestamp("2026-08-31 23:59:59")
TEST_START = pd.Timestamp("2026-09-01 00:00:00")
SEQUENCE_LENGTH = 60
FEATURE_NAMES = (
    "log_return_1",
    "log_return_4",
    "log_return_16",
    "candle_body",
    "high_low_range",
    "log_volume_change",
    "realized_volatility_16",
    "sma_20_50_spread",
    "taker_buy_share",
)


@dataclass(frozen=True)
class SampleKey:
    timestamp: pd.Timestamp
    instrument: str


@dataclass(frozen=True)
class PreparedFrames:
    raw: pd.DataFrame
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame
    test_keys: tuple[SampleKey, ...]
    feature_mean: tuple[float, ...]
    feature_std: tuple[float, ...]


def _utc_naive(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        return index
    return index.tz_convert("UTC").tz_localize(None)


def _validate_candles(candles: pd.DataFrame) -> pd.DataFrame:
    required = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "taker_buy_base_volume",
        "is_closed",
        "is_imputed",
    }
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")
    data = candles.sort_values("timestamp").copy()
    if data.empty or data["timestamp"].duplicated().any():
        raise ValueError("candles must be non-empty with unique timestamps")
    if not bool(data["is_closed"].all()) or bool(data["is_imputed"].any()):
        raise ValueError("only closed, non-imputed candles are accepted")
    numeric = data[
        [
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "taker_buy_base_volume",
        ]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("candle values must be finite")
    if bool((data[["open", "high", "low", "close"]] <= 0).any().any()):
        raise ValueError("prices must be positive")
    if bool((data[["base_volume", "taker_buy_base_volume"]] < 0).any().any()):
        raise ValueError("volumes must not be negative")
    return data


def resample_candles(candles: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return complete OHLCV groups without filling missing observations."""
    data = _validate_candles(candles).set_index("timestamp")
    data.index = _utc_naive(pd.DatetimeIndex(data.index))
    group_size, _ = TIMEFRAMES[timeframe]
    if group_size == 1:
        return data
    grouped = data.resample(timeframe).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        base_volume=("base_volume", "sum"),
        taker_buy_base_volume=("taker_buy_base_volume", "sum"),
        count=("close", "count"),
    )
    result = grouped.loc[grouped["count"] == group_size].drop(columns="count")
    result["is_closed"] = True
    result["is_imputed"] = False
    return result


def causal_feature_rows(candles: pd.DataFrame) -> pd.DataFrame:
    """Build features at each candle close using no later observation."""
    close = candles["close"].astype(float)
    log_close = np.log(close)
    volume = candles["base_volume"].astype(float)
    taker = candles["taker_buy_base_volume"].astype(float)
    features = pd.DataFrame(index=candles.index)
    features["log_return_1"] = log_close.diff(1)
    features["log_return_4"] = log_close.diff(4)
    features["log_return_16"] = log_close.diff(16)
    features["candle_body"] = np.log(close / candles["open"].astype(float))
    features["high_low_range"] = np.log(
        candles["high"].astype(float) / candles["low"].astype(float)
    )
    features["log_volume_change"] = np.log1p(volume).diff(1)
    features["realized_volatility_16"] = features["log_return_1"].rolling(16).std()
    features["sma_20_50_spread"] = (
        close.rolling(20).mean() / close.rolling(50).mean() - 1.0
    )
    denominator = volume.replace(0.0, np.nan)
    features["taker_buy_share"] = (taker / denominator).fillna(0.5) - 0.5
    return features.loc[:, FEATURE_NAMES]


def supervised_rows(candles: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Align causal features with the following candle's percent return label."""
    features = causal_feature_rows(candles)
    close = candles["close"].astype(float)
    target_index = pd.DatetimeIndex(candles.index[1:])
    aligned_features = features.iloc[:-1].copy()
    aligned_features.index = target_index
    labels = (close.shift(-1) / close - 1.0).iloc[:-1] * 100.0
    labels.index = target_index
    valid = aligned_features.notna().all(axis=1) & labels.notna()
    return aligned_features.loc[valid], labels.loc[valid].rename("LABEL0")


def chronological_split(timestamp: pd.Timestamp) -> str:
    value = pd.Timestamp(timestamp)
    if value.tz is not None:
        value = value.tz_convert("UTC").tz_localize(None)
    if value <= TRAIN_END:
        return "train"
    if VALID_START <= value <= VALID_END:
        return "valid"
    if value >= TEST_START:
        return "test"
    raise ValueError(f"timestamp falls outside locked segments: {value}")


def flatten_feature_sequence(frame: pd.DataFrame) -> np.ndarray:
    """Flatten as feature-major values required by Qlib LSTM/ALSTM/Transformer."""
    if tuple(frame.columns) != FEATURE_NAMES or len(frame) != SEQUENCE_LENGTH:
        raise ValueError("invalid feature sequence shape or columns")
    values = frame.to_numpy(dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("feature sequence must be finite")
    return values.T.reshape(-1)


def _collection_sha256(root: Path) -> str:
    lines = []
    for path in root.glob("*.parquet"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    if not lines:
        raise ValueError("no Parquet inputs found")
    digest = hashlib.sha256()
    for line in sorted(lines):
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def _load_symbol(root: Path, symbol: str) -> pd.DataFrame:
    import pyarrow.parquet as pq

    files = sorted(root.glob(f"{symbol}-*.parquet"), key=lambda path: path.name)
    if not files:
        raise ValueError(f"missing inputs for {symbol}")
    frames = [pq.read_table(path).to_pandas() for path in files]
    data = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    data = _validate_candles(data)
    deltas = data["timestamp"].diff().dropna()
    if bool((deltas != pd.Timedelta(minutes=15)).any()):
        raise ValueError(f"15m gaps for {symbol}")
    return data


def _test_targets(length: int, timeframe: str, origins: int) -> set[int]:
    _, spacing = TIMEFRAMES[timeframe]
    return set(
        chronological_origins(
            length,
            context_length=512,
            count=origins,
            spacing=spacing,
        )
    )


def prepare_frames(
    root: Path,
    *,
    origins: int = 8,
    train_limit_per_instrument: int = 6_000,
    valid_limit_per_instrument: int = 1_000,
) -> PreparedFrames:
    """Create locked, normalized Qlib frames and exact Chronos test keys."""
    if origins < 1 or train_limit_per_instrument < 1 or valid_limit_per_instrument < 1:
        raise ValueError("sample limits and origins must be positive")
    by_instrument: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    test_keys: list[SampleKey] = []
    for symbol in SYMBOLS:
        base = _load_symbol(root, symbol)
        for timeframe in TIMEFRAMES:
            candles = resample_candles(base, timeframe)
            features, labels = supervised_rows(candles)
            instrument = f"{symbol}_{timeframe}"
            by_instrument[instrument] = (features, labels)
            targets = _test_targets(len(candles), timeframe, origins)
            for target in sorted(targets):
                timestamp = pd.Timestamp(candles.index[target])
                if chronological_split(timestamp) != "test":
                    raise ValueError("locked test origin is outside the test segment")
                test_keys.append(SampleKey(timestamp, instrument))

    training_rows = pd.concat(
        [features.loc[features.index <= TRAIN_END] for features, _ in by_instrument.values()]
    )
    mean = training_rows.mean()
    std = training_rows.std().replace(0.0, np.nan)
    if not np.isfinite(mean.to_numpy()).all() or not np.isfinite(std.to_numpy()).all():
        raise ValueError("training-only normalization statistics must be finite")

    raw_parts = []
    sequence_parts: dict[str, list[pd.DataFrame]] = {
        "train": [],
        "valid": [],
        "test": [],
    }
    test_key_set = {(key.timestamp, key.instrument) for key in test_keys}
    for instrument, (features, labels) in by_instrument.items():
        normalized = (features - mean) / std
        raw = normalized.copy()
        raw["LABEL0"] = labels
        raw["instrument"] = instrument
        raw = raw.set_index("instrument", append=True)
        raw.index.names = ["datetime", "instrument"]
        raw_parts.append(raw)

        columns = [
            f"{name}_lag_{lag:02d}"
            for name in FEATURE_NAMES
            for lag in reversed(range(SEQUENCE_LENGTH))
        ]
        values = normalized.to_numpy(dtype=np.float32)
        windows = np.lib.stride_tricks.sliding_window_view(
            values,
            window_shape=SEQUENCE_LENGTH,
            axis=0,
        )
        eligible = np.arange(SEQUENCE_LENGTH - 1, len(normalized))
        timestamps = pd.DatetimeIndex(normalized.index)
        positions = {
            "train": eligible[timestamps[eligible] <= TRAIN_END][
                -train_limit_per_instrument:
            ],
            "valid": eligible[
                (timestamps[eligible] >= VALID_START)
                & (timestamps[eligible] <= VALID_END)
            ][-valid_limit_per_instrument:],
            "test": np.array(
                [
                    position
                    for position in eligible
                    if (pd.Timestamp(timestamps[position]), instrument) in test_key_set
                ],
                dtype=int,
            ),
        }
        for segment, selected in positions.items():
            matrix = windows[selected - SEQUENCE_LENGTH + 1].reshape(
                len(selected), len(FEATURE_NAMES) * SEQUENCE_LENGTH
            )
            index = pd.MultiIndex.from_arrays(
                [timestamps[selected], [instrument] * len(selected)],
                names=["datetime", "instrument"],
            )
            frame = pd.DataFrame(matrix, index=index, columns=columns)
            frame["LABEL0"] = labels.iloc[selected].to_numpy(dtype=float)
            sequence_parts[segment].append(frame)

    raw_frame = pd.concat(raw_parts).sort_index()
    train = pd.concat(sequence_parts["train"]).sort_index()
    valid = pd.concat(sequence_parts["valid"]).sort_index()
    test = pd.concat(sequence_parts["test"]).sort_index()
    expected = origins * len(SYMBOLS) * len(TIMEFRAMES)
    if len(test) != expected or len(test_keys) != expected:
        raise ValueError("test frame does not match the locked Chronos origins")
    for frame in (raw_frame, train, valid, test):
        numeric = frame.to_numpy(dtype=float)
        if not np.isfinite(numeric).all():
            raise ValueError("prepared Qlib frames must be finite")
    if not (train.index.get_level_values("datetime").max() <= TRAIN_END):
        raise ValueError("train boundary violation")
    valid_times = valid.index.get_level_values("datetime")
    if valid_times.min() < VALID_START or valid_times.max() > VALID_END:
        raise ValueError("validation boundary violation")
    return PreparedFrames(
        raw=raw_frame,
        train=train,
        valid=valid,
        test=test,
        test_keys=tuple(sorted(test_keys, key=lambda key: (key.timestamp, key.instrument))),
        feature_mean=tuple(float(mean[name]) for name in FEATURE_NAMES),
        feature_std=tuple(float(std[name]) for name in FEATURE_NAMES),
    )


def validate_prediction_values(values: Iterable[float], *, expected: int) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != expected or any(not math.isfinite(value) for value in result):
        raise ValueError("predictions must have the expected finite values")
    return result

