"""Materialize E001-equivalent causal samples for one frozen walk-forward fold.

No GPU, filesystem writes, checkpoint loading or trading integration.
Model fitting and validation-only checkpoint selection belong to later tasks.
"""

from __future__ import annotations

import numpy as np

from .dataset import ResearchDataset, Samples, causal_ohlcv_features
from .walk_forward import Fold, Stage


def _validate_fold(
    fold: Fold, *, candle_count: int, lookback: int, horizon: int,
) -> None:
    """Fail closed for edited or stale fold bounds before indexing arrays."""
    if not isinstance(fold, Fold) or type(fold.number) is not int or fold.number < 1:
        raise ValueError("invalid fold identity")
    if not (type(lookback) is int and 8 <= lookback <= 256
            and type(horizon) is int and 1 <= horizon <= 24):
        raise ValueError("lookback must be 8..256 and horizon 1..24")

    train, val, test = fold.train, fold.validation, fold.test
    if not all(isinstance(stage, Stage) for stage in (train, val, test)):
        raise ValueError("fold contains an invalid stage")
    if train.anchor_start != lookback - 1:
        raise ValueError("lookback does not match the frozen fold plan")
    if any(s.anchor_stop != s.bar_stop - horizon for s in (train, val, test)):
        raise ValueError("horizon does not match the frozen fold plan")
    if not (
        train.bar_start == 0
        and train.bar_stop == val.bar_start
        and val.bar_stop == test.bar_start
        and 0 < train.bar_stop < val.bar_stop < test.bar_stop <= candle_count
    ):
        raise ValueError("fold stages must be ordered, contiguous and in range")

    for name, stage, first_anchor, minimum in (
        ("train", train, lookback - 1, 32),
        ("validation", val, val.bar_start, 5),
        ("test", test, test.bar_start, 5),
    ):
        if any(type(value) is not int for value in (
            stage.bar_start, stage.bar_stop, stage.anchor_start,
            stage.anchor_stop, stage.first_target, stage.last_target
        )):
            raise ValueError(f"{name} stage must have integral bounds")
        last_exclusive = stage.bar_stop - horizon
        if not (
            stage.anchor_start == first_anchor
            and stage.anchor_stop == last_exclusive
            and stage.anchor_stop - stage.anchor_start >= minimum
            and stage.first_target == stage.anchor_start + horizon
            and stage.last_target == stage.anchor_stop - 1 + horizon
            and stage.last_target < stage.bar_stop
            and stage.anchor_start >= 0
        ):
            raise ValueError(f"{name} stage has unsafe or inconsistent purged bounds")


def make_fold_samples(
    dataset: ResearchDataset,
    fold: Fold,
    *,
    lookback: int = 32,
    horizon: int = 1,
) -> Samples:
    """Prepare one fold's samples, fitting *only* on that fold's training data.

    Training anchor t sees candles <=t; its label is close[t+horizon].
    The scaler observes feature rows 0..(last training anchor), **not**
    future validation/test feature rows and not the training labels.

    Validation/test windows can read causal feature context across their
    left boundary; they never read candles after each anchor or contribute
    to scaler fitting. No arrays are persisted or sent to a trading system.
    """
    n = len(dataset.close)
    if len(dataset.times) != n:
        raise ValueError("dataset timestamps and close have different lengths")
    _validate_fold(fold, candle_count=n, lookback=lookback, horizon=horizon)
    features = causal_ohlcv_features(dataset)
    train_feature_stop = fold.train.anchor_stop
    train_features = features[:train_feature_stop]
    mean = train_features.mean(axis=0, dtype=np.float64)
    std = np.maximum(train_features.std(axis=0, dtype=np.float64), 1e-8)
    if not np.isfinite(mean).all() or not np.isfinite(std).all():
        raise ValueError("non-finite train-only normalizer")
    # Restrict materialized features to the active fold's known history.
    normalized = np.clip(
        (features[:fold.test.bar_stop] - mean) / std, -10.0, 10.0
    )
    close = dataset.close

    def as_arrays(stage: Stage) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        anchors = np.arange(stage.anchor_start, stage.anchor_stop, dtype=np.int64)
        # This slice is causal; data at or after anchor+1 is never included.
        x = np.stack([
            normalized[t - lookback + 1:t + 1].astype(np.float32)
            for t in anchors
        ])
        y = np.log(close[anchors + horizon] / close[anchors]).astype(np.float32)
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("non-finite feature or target in fold")
        return x, y, anchors

    tr_x, tr_y, tr_a = as_arrays(fold.train)
    va_x, va_y, va_a = as_arrays(fold.validation)
    te_x, te_y, te_a = as_arrays(fold.test)
    return Samples(
        train_x=tr_x, train_y=tr_y, val_x=va_x, val_y=va_y,
        test_x=te_x, test_y=te_y,
        train_anchors=tr_a, val_anchors=va_a, test_anchors=te_a,
        feature_mean=mean, feature_std=std,
        train_cut=fold.train.bar_stop, val_cut=fold.validation.bar_stop,
    )
