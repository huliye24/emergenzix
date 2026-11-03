from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from emergenzix_api.qlib_gpu_benchmark import (
    FEATURE_NAMES,
    SEQUENCE_LENGTH,
    TRAIN_END,
    VALID_END,
    VALID_START,
    causal_feature_rows,
    chronological_split,
    flatten_feature_sequence,
    supervised_rows,
    validate_prediction_values,
    _utc_naive,
)


def _candles(count: int = 100) -> pd.DataFrame:
    timestamp = pd.date_range("2026-01-01", periods=count, freq="15min", tz="UTC")
    close = 100.0 + np.arange(count) * 0.1
    return pd.DataFrame(
        {
            "open": close - 0.03,
            "high": close + 0.07,
            "low": close - 0.08,
            "close": close,
            "base_volume": 10.0 + np.arange(count),
            "taker_buy_base_volume": 5.0 + np.arange(count) * 0.5,
            "is_closed": True,
            "is_imputed": False,
        },
        index=timestamp,
    )


def test_feature_row_is_unchanged_when_later_candle_changes() -> None:
    candles = _candles()
    original = causal_feature_rows(candles).iloc[70].copy()
    changed = candles.copy()
    changed.loc[changed.index[80], "close"] *= 2.0

    pd.testing.assert_series_equal(original, causal_feature_rows(changed).iloc[70])


def test_supervised_label_uses_following_close() -> None:
    candles = _candles()
    features, labels = supervised_rows(candles)
    target = candles.index[60]

    assert target in features.index
    assert labels.loc[target] == pytest.approx(
        (candles.loc[target, "close"] / candles.iloc[59]["close"] - 1.0) * 100.0
    )


def test_locked_segments_do_not_overlap() -> None:
    assert chronological_split(TRAIN_END) == "train"
    assert chronological_split(VALID_START) == "valid"
    assert chronological_split(VALID_END) == "valid"
    assert chronological_split(pd.Timestamp("2026-09-01")) == "test"


def test_flattening_is_feature_major_for_qlib_models() -> None:
    frame = pd.DataFrame(
        np.arange(SEQUENCE_LENGTH * len(FEATURE_NAMES)).reshape(
            SEQUENCE_LENGTH, len(FEATURE_NAMES)
        ),
        columns=FEATURE_NAMES,
    )
    result = flatten_feature_sequence(frame)

    assert result.shape == (SEQUENCE_LENGTH * len(FEATURE_NAMES),)
    assert np.array_equal(result[:SEQUENCE_LENGTH], frame[FEATURE_NAMES[0]].to_numpy())


def test_prediction_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="expected finite"):
        validate_prediction_values([0.1, float("nan")], expected=2)


def test_utc_naive_normalizes_datetime_precision() -> None:
    source = pd.date_range("2026-01-01", periods=2, freq="15min", tz="UTC").as_unit("us")
    result = _utc_naive(source)

    assert result.tz is None
    assert result.dtype == "datetime64[ns]"

