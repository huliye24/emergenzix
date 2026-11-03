from __future__ import annotations

import pandas as pd
import pytest

from emergenzix_api.qlib_gpu_benchmark import SampleKey
from emergenzix_api.qlib_gpu_train import (
    FrameDataset,
    ordered_predictions,
    score_by_instrument,
)


def _frame(index: pd.MultiIndex) -> pd.DataFrame:
    return pd.DataFrame({"f0": [1.0] * len(index), "LABEL0": [0.5] * len(index)}, index=index)


def test_frame_dataset_exposes_qlib_column_groups() -> None:
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-01"), "btcusdt_1h")],
        names=["datetime", "instrument"],
    )
    frames = type("Frames", (), {"train": _frame(index), "valid": _frame(index), "test": _frame(index)})
    dataset = FrameDataset(frames)

    prepared = dataset.prepare("train", col_set=["feature", "label"])
    assert list(prepared["feature"].columns) == ["f0"]
    assert list(prepared["label"].columns) == ["LABEL0"]
    assert list(dataset.prepare("test", col_set="feature").columns) == ["f0"]


def test_ordered_predictions_require_every_locked_key() -> None:
    key = SampleKey(pd.Timestamp("2026-09-01"), "btcusdt_1h")
    index = pd.MultiIndex.from_tuples(
        [(key.timestamp, key.instrument)], names=["datetime", "instrument"]
    )
    prediction = pd.Series([1.25], index=index)

    assert ordered_predictions(prediction, [key]) == [0.0125]
    with pytest.raises(ValueError, match="missing prediction"):
        ordered_predictions(prediction, [SampleKey(key.timestamp, "ethusdt_1h")])


def test_scoring_keeps_each_symbol_timeframe_separate() -> None:
    keys = (
        SampleKey(pd.Timestamp("2026-09-01"), "btcusdt_1h"),
        SampleKey(pd.Timestamp("2026-09-02"), "ethusdt_4h"),
    )
    result = score_by_instrument(
        [0.01, -0.02],
        [0.02, -0.01],
        keys,
        round_trip_cost_bps=30,
    )

    assert result["overall"]["direction_accuracy_pct"] == 100.0
    assert sorted(result["by_symbol_timeframe"]) == ["btcusdt:1h", "ethusdt:4h"]

