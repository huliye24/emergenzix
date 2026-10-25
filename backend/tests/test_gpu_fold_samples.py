"""CPU-only regression tests for GPU fold-aware samples and leakage limits."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from emergenzix_gpu_lab.dataset import (
    ResearchDataset, causal_ohlcv_features, make_samples,
)
from emergenzix_gpu_lab.fold_samples import make_fold_samples
from emergenzix_gpu_lab.walk_forward import plan_walk_forward


def _history(count: int = 1100) -> ResearchDataset:
    index = np.arange(count, dtype=np.float64)
    close = 100.0 * np.exp(0.0001 * index + 0.009 * np.sin(index / 9.0))
    volume = 1000 + (index % 17) * 3 + 12 * np.sin(index / 4.0)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return ResearchDataset(
        dataset_id="c" * 64,
        symbol="BTC/USDT",
        timeframe="1h",
        times=tuple(
            (start + timedelta(hours=i)).isoformat()
            for i in range(count)
        ),
        close=close,
        open=close * 0.999,
        high=close * 1.01,
        low=close * 0.99,
        volume=volume,
    )


def test_fold_materialization_shapes_boundaries_and_targets():
    data = _history()
    folds = plan_walk_forward(len(data.times), lookback=32, horizon=3)
    for fold in folds:
        samples = make_fold_samples(data, fold, lookback=32, horizon=3)
        assert samples.train_cut == fold.train.bar_stop
        assert samples.val_cut == fold.validation.bar_stop
        assert samples.train_x.shape == (fold.train.sample_count, 32, 4)
        assert samples.val_x.shape == (fold.validation.sample_count, 32, 4)
        assert samples.test_x.shape == (fold.test.sample_count, 32, 4)
        assert samples.train_x.dtype == np.float32
        assert samples.train_y.dtype == np.float32
        assert samples.feature_mean.shape == (4,)
        assert samples.feature_std.shape == (4,)
        for stage, anchors, targets in (
            (fold.train, samples.train_anchors, samples.train_y),
            (fold.validation, samples.val_anchors, samples.val_y),
            (fold.test, samples.test_anchors, samples.test_y),
        ):
            np.testing.assert_array_equal(
                anchors,
                np.arange(stage.anchor_start, stage.anchor_stop),
            )
            assert anchors[-1] + 3 == stage.last_target
            assert anchors[-1] + 3 < stage.bar_stop
            np.testing.assert_allclose(
                targets.astype(np.float64),
                np.log(data.close[anchors + 3] / data.close[anchors]),
                rtol=1e-6, atol=1e-8,
            )


def test_fold_uses_identical_e001_causal_feature_semantics():
    data = _history()
    features = causal_ohlcv_features(data)
    previous_close = np.r_[data.close[0], data.close[:-1]]
    previous_volume = np.r_[data.volume[0], data.volume[:-1]]
    manual = np.column_stack((
        np.log(data.close / previous_close),
        np.log(data.high / data.low),
        np.log(data.close / data.open),
        np.log1p(data.volume) - np.log1p(previous_volume),
    ))
    np.testing.assert_array_equal(features, manual)
    e001 = make_samples(data, lookback=32, horizon=3)
    expected_mean = manual[:e001.train_anchors[-1] + 1].mean(axis=0)
    np.testing.assert_allclose(e001.feature_mean, expected_mean, atol=0, rtol=0)

    fold = plan_walk_forward(len(data.times), lookback=32, horizon=3)[0]
    samples = make_fold_samples(data, fold, lookback=32, horizon=3)
    train_features = manual[:fold.train.anchor_stop]
    mean = train_features.mean(axis=0)
    std = np.maximum(train_features.std(axis=0), 1e-8)
    np.testing.assert_allclose(samples.feature_mean, mean, atol=0, rtol=0)
    np.testing.assert_allclose(samples.feature_std, std, atol=0, rtol=0)
    expected_first_window = np.clip(
        (manual[:32] - mean) / std, -10, 10
    ).astype(np.float32)
    np.testing.assert_array_equal(samples.train_x[0], expected_first_window)
    first_validation_anchor = fold.validation.anchor_start
    expected_validation_window = np.clip(
        (manual[first_validation_anchor - 31:first_validation_anchor + 1]
         - mean) / std, -10, 10
    ).astype(np.float32)
    np.testing.assert_array_equal(samples.val_x[0], expected_validation_window)


def test_future_validation_test_mutations_never_alter_fold_training():
    data = _history()
    fold = plan_walk_forward(len(data.times))[0]
    original = make_fold_samples(data, fold)
    future_close = data.close.copy()
    future_volume = data.volume.copy()
    future_count = len(future_close) - fold.validation.bar_start
    future_close[fold.validation.bar_start:] *= (
        1.4 + 0.03 * np.sin(np.arange(future_count) / 3.0)
    )
    future_volume[fold.validation.bar_start:] *= 2.0
    changed_data = replace(
        data, close=future_close, volume=future_volume,
    )
    after = make_fold_samples(changed_data, fold)
    np.testing.assert_array_equal(original.feature_mean, after.feature_mean)
    np.testing.assert_array_equal(original.feature_std, after.feature_std)
    np.testing.assert_array_equal(original.train_x, after.train_x)
    np.testing.assert_array_equal(original.train_y, after.train_y)
    np.testing.assert_array_equal(original.train_anchors, after.train_anchors)
    assert not np.array_equal(original.val_x, after.val_x)
    assert not np.array_equal(original.test_y, after.test_y)


def test_test_only_changes_do_not_touch_validation_or_first_test_anchor():
    data = _history()
    fold = plan_walk_forward(len(data.times), horizon=2)[0]
    before = make_fold_samples(data, fold, horizon=2)
    new_close = data.close.copy()
    # Change after first test anchor (the first test target will change).
    new_close[fold.test.anchor_start + 1:] *= 3
    after = make_fold_samples(
        replace(data, close=new_close), fold, horizon=2,
    )
    np.testing.assert_array_equal(before.feature_mean, after.feature_mean)
    np.testing.assert_array_equal(before.train_x, after.train_x)
    np.testing.assert_array_equal(before.val_x, after.val_x)
    np.testing.assert_array_equal(before.val_y, after.val_y)
    np.testing.assert_array_equal(before.test_x[0], after.test_x[0])
    assert before.test_y[0] != after.test_y[0]


def test_per_fold_scaler_expands_only_when_earlier_test_becomes_history():
    data = _history()
    f1, f2, f3 = plan_walk_forward(len(data.times))
    before1 = make_fold_samples(data, f1)
    before2 = make_fold_samples(data, f2)
    before3 = make_fold_samples(data, f3)
    changed_close = data.close.copy()
    changed_close[f1.test.bar_start:f1.test.bar_stop] *= 1.7
    modified = replace(data, close=changed_close)
    after1 = make_fold_samples(modified, f1)
    after2 = make_fold_samples(modified, f2)
    after3 = make_fold_samples(modified, f3)
    np.testing.assert_array_equal(before1.feature_mean, after1.feature_mean)
    np.testing.assert_array_equal(before2.feature_mean, after2.feature_mean)
    assert not np.array_equal(before3.feature_mean, after3.feature_mean)


@pytest.mark.parametrize("field", [
    "anchor_start", "anchor_stop", "first_target", "last_target",
])
def test_tampered_purged_bounds_are_rejected(field):
    data = _history()
    fold = plan_walk_forward(len(data.times))[0]
    altered_stage = replace(
        fold.validation, **{field: getattr(fold.validation, field) + 1}
    )
    bad_fold = replace(fold, validation=altered_stage)
    with pytest.raises(ValueError, match="unsafe or inconsistent"):
        make_fold_samples(data, bad_fold)


def test_wrong_hyperparameters_wrong_dataset_and_gaps_fail_closed():
    data = _history()
    fold = plan_walk_forward(len(data.times))[0]
    with pytest.raises(ValueError, match="lookback"):
        make_fold_samples(data, fold, lookback=16)
    with pytest.raises(ValueError, match="horizon"):
        make_fold_samples(data, fold, horizon=2)
    with pytest.raises(ValueError, match="different lengths"):
        make_fold_samples(replace(data, times=data.times[:-1]), fold)
    with pytest.raises(ValueError, match="inconsistent OHLCV array"):
        make_fold_samples(replace(data, volume=data.volume[:-1]), fold)
    with pytest.raises(ValueError, match="non-finite OHLCV"):
        corrupt = data.close.copy()
        corrupt[-1] = float("nan")
        make_fold_samples(replace(data, close=corrupt), fold)


def test_read_only_build_is_pure_and_does_not_change_input():
    data = _history()
    before = {
        key: getattr(data, key).copy()
        for key in ("open", "high", "low", "close", "volume")
    }
    fold = plan_walk_forward(len(data.times))[0]
    a = make_fold_samples(data, fold)
    b = make_fold_samples(data, fold)
    np.testing.assert_array_equal(a.train_x, b.train_x)
    np.testing.assert_array_equal(a.feature_mean, b.feature_mean)
    for key, previous in before.items():
        np.testing.assert_array_equal(getattr(data, key), previous)
