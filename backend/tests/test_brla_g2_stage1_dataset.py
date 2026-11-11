from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.brla_g2_stage1_dataset import (
    FEATURE_NAMES, build_stage1_dataset, build_stage1_example, dataset_manifest,
    dataset_protocol_manifest,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose
from emergenzix_api.brmg_scenarios import Partition

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(days=188):
    rows, price = [], 200.0
    for index in range(days * 96):
        opening = price
        price *= 1 + (-.00008 if index % 5 else .00003)
        rows.append({"timestamp": (START + timedelta(minutes=15 * index)).isoformat(),
            "open": opening, "high": max(opening, price) * 1.0002,
            "low": min(opening, price) * .9998, "close": price,
            "base_volume": 1000 + index % 17,
            "is_closed": True, "is_imputed": False})
    return rows


@pytest.fixture(scope="module")
def train_dataset():
    return build_stage1_dataset(bars(), scenario_sha256="a" * 64,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE1_TRAIN,
        stride_bars=96)


def test_dataset_has_separate_fixed_shape_features_and_future_labels(train_dataset):
    example = train_dataset.examples[0]
    assert example.feature_names == FEATURE_NAMES
    assert {name: (len(values), len(values[0]))
            for name, values in example.timeframe_features.items()} == {
                "15m": (192, 4), "1h": (168, 4), "4h": (180, 4), "1d": (180, 4)}
    assert datetime.fromisoformat(example.as_of) < datetime.fromisoformat(
        example.label.label_horizon_end)
    assert example.future_raw_bars_included is False
    assert not hasattr(example, "future_bars")


def test_dataset_and_examples_are_deterministic_and_hashed(train_dataset):
    second = build_stage1_dataset(bars(), scenario_sha256="a" * 64,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE1_TRAIN,
        stride_bars=96)
    assert second.dataset_sha256 == train_dataset.dataset_sha256
    assert second.example_sha256s == train_dataset.example_sha256s
    assert len(set(second.example_sha256s)) == len(second.example_sha256s)


def test_future_change_changes_label_not_historical_features():
    source = bars()
    anchor = START + timedelta(days=180)
    first = build_stage1_example(source, as_of=anchor,
        scenario_sha256="b" * 64, partition=Partition.TRAIN)
    changed = deepcopy(source)
    future_start = 180 * 96
    for row in changed[future_start:future_start + 7 * 96]:
        row["close"] *= 1.01
        row["high"] = max(row["high"], row["close"] * 1.0001)
    second = build_stage1_example(changed, as_of=anchor,
        scenario_sha256="b" * 64, partition=Partition.TRAIN)
    assert first.observation_sha256 == second.observation_sha256
    assert first.timeframe_features == second.timeframe_features
    assert first.label.label_sha256 != second.label.label_sha256


def test_partition_purpose_cannot_be_mixed():
    with pytest.raises(PermissionError):
        build_stage1_dataset(bars(), scenario_sha256="c" * 64,
            partition=Partition.DEVELOPMENT, purpose=DataPurpose.STAGE1_TRAIN)
    with pytest.raises(PermissionError):
        build_stage1_dataset(bars(), scenario_sha256="c" * 64,
            partition=Partition.SEALED_TEST, purpose=DataPurpose.FINAL_SEALED_EVAL)


def test_sealed_dataset_requires_frozen_model_and_external_authorization():
    sealed = build_stage1_dataset(bars(), scenario_sha256="d" * 64,
        partition=Partition.SEALED_TEST, purpose=DataPurpose.FINAL_SEALED_EVAL,
        stride_bars=768, model_frozen=True, sealed_access_authorized=True)
    assert sealed.partition is Partition.SEALED_TEST
    assert sealed.purpose is DataPurpose.FINAL_SEALED_EVAL


def test_manifest_contains_shapes_but_no_future_raw_data(train_dataset):
    manifest = dataset_manifest(train_dataset)
    assert manifest["timeframe_shapes"]["4h"] == [180, 4]
    assert manifest["future_raw_bars_included"] is False
    assert manifest["position_permission"] is False
    assert len(manifest["dataset_sha256"]) == 64
    protocol = dataset_protocol_manifest()
    assert protocol["allowed_partition_purpose"]["SEALED_TEST"] == "FINAL_SEALED_EVAL"
    assert len(protocol["protocol_sha256"]) == 64
