"""Causal stage-one dataset joining frozen observations to future-only labels."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .brla_g2_multitimeframe import (
    BASE_INTERVAL, MultiTimeframeObservation, WINDOW_BARS,
    build_multitimeframe_observation, stage1_feature_fingerprints,
)
from .brla_g2_order_labels import (
    FUTURE_BAR_COUNT, CompetitionLabel, label_order_competition,
)
from .brla_g2_training_protocol import (
    DataPurpose, Stage1Observation, authorize_data_use,
)
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-stage1-dataset-v1"
FEATURE_NAMES = ("log_return", "log_range", "log_body", "log_volume_ratio")


@dataclass(frozen=True, slots=True)
class Stage1Example:
    scenario_sha256: str
    partition: Partition
    as_of: str
    feature_names: tuple[str, ...]
    timeframe_features: Mapping[str, tuple[tuple[float, ...], ...]]
    timeframe_feature_sha256: Mapping[str, str]
    observation_sha256: str
    label: CompetitionLabel
    example_sha256: str
    future_raw_bars_included: bool = False
    position_permission: bool = False
    order_permission: bool = False


@dataclass(frozen=True, slots=True)
class Stage1Dataset:
    protocol: str
    scenario_sha256: str
    partition: Partition
    purpose: DataPurpose
    stride_bars: int
    examples: tuple[Stage1Example, ...]
    example_sha256s: tuple[str, ...]
    dataset_sha256: str
    automatic_promotion: bool = False
    position_permission: bool = False
    order_permission: bool = False


def _parse(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _features(observation: MultiTimeframeObservation
              ) -> dict[str, tuple[tuple[float, ...], ...]]:
    result = {}
    for timeframe, window in observation.windows.items():
        bars = window.bars
        volumes = [math.log(bar.base_volume) for bar in bars]
        volume_center = statistics.median(volumes)
        rows = []
        for index, bar in enumerate(bars):
            previous = bars[index - 1].close if index else bar.open
            row = (math.log(bar.close / previous), math.log(bar.high / bar.low),
                   math.log(bar.close / bar.open), math.log(bar.base_volume) - volume_center)
            if any(not math.isfinite(value) for value in row):
                raise ValueError("derived stage-one features must be finite")
            rows.append(row)
        result[timeframe] = tuple(rows)
    return result


def _example_payload(example: Stage1Example) -> dict[str, Any]:
    payload = asdict(example)
    payload["partition"] = example.partition.value
    payload["label"]["partition"] = example.label.partition.value
    payload["label"]["old_direction"] = example.label.old_direction.value
    payload["label"]["state"] = example.label.state.value
    payload["example_sha256"] = ""
    return payload


def build_stage1_example(source_bars: Sequence[Mapping[str, Any]], *,
                         as_of: datetime, scenario_sha256: str,
                         partition: Partition) -> Stage1Example:
    observation = build_multitimeframe_observation(source_bars, as_of=as_of)
    decision_time = _parse(as_of)
    future = [row for row in source_bars
              if decision_time <= _parse(row["timestamp"])
              < decision_time + BASE_INTERVAL * FUTURE_BAR_COUNT]
    label = label_order_competition(observation, future,
        scenario_sha256=scenario_sha256, partition=partition)
    fingerprints = stage1_feature_fingerprints(observation)
    stage1_input = Stage1Observation(scenario_sha256=scenario_sha256,
        as_of=decision_time, feature_end=decision_time,
        timeframe_feature_sha256=fingerprints, partition=partition)
    features = _features(observation)
    base = Stage1Example(scenario_sha256, partition, decision_time.isoformat(),
        FEATURE_NAMES, features, stage1_input.timeframe_feature_sha256,
        observation.observation_sha256, label, "")
    digest = hashlib.sha256(json.dumps(_example_payload(base), sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return Stage1Example(scenario_sha256, partition, decision_time.isoformat(),
        FEATURE_NAMES, features, stage1_input.timeframe_feature_sha256,
        observation.observation_sha256, label, digest)


def build_stage1_dataset(source_bars: Sequence[Mapping[str, Any]], *,
                         scenario_sha256: str, partition: Partition,
                         purpose: DataPurpose, stride_bars: int = 96,
                         model_frozen: bool = False,
                         sealed_access_authorized: bool = False) -> Stage1Dataset:
    authorize_data_use(partition, purpose, model_frozen=model_frozen,
                       sealed_access_authorized=sealed_access_authorized)
    if stride_bars < 1 or isinstance(stride_bars, bool):
        raise ValueError("stride_bars must be a positive integer")
    ordered = sorted(source_bars, key=lambda row: _parse(row["timestamp"]))
    if not ordered:
        raise ValueError("source bars are required")
    first = _parse(ordered[0]["timestamp"])
    last_close = _parse(ordered[-1]["timestamp"]) + BASE_INTERVAL
    first_anchor = first + timedelta(days=WINDOW_BARS["1d"])
    final_anchor = last_close - BASE_INTERVAL * FUTURE_BAR_COUNT
    anchors = []
    cursor = first_anchor
    stride = BASE_INTERVAL * stride_bars
    while cursor <= final_anchor:
        anchors.append(cursor); cursor += stride
    if not anchors:
        raise ValueError("source does not cover lookback plus future label horizon")
    examples = tuple(build_stage1_example(ordered, as_of=anchor,
        scenario_sha256=scenario_sha256, partition=partition) for anchor in anchors)
    hashes = tuple(example.example_sha256 for example in examples)
    identity = {"protocol": PROTOCOL, "scenario_sha256": scenario_sha256,
        "partition": partition.value, "purpose": purpose.value,
        "stride_bars": stride_bars, "example_sha256s": hashes,
        "automatic_promotion": False, "position_permission": False,
        "order_permission": False}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return Stage1Dataset(PROTOCOL, scenario_sha256, partition, purpose,
                         stride_bars, examples, hashes, digest)


def dataset_manifest(dataset: Stage1Dataset) -> dict[str, Any]:
    return {"protocol": dataset.protocol, "scenario_sha256": dataset.scenario_sha256,
        "partition": dataset.partition.value, "purpose": dataset.purpose.value,
        "stride_bars": dataset.stride_bars, "example_count": len(dataset.examples),
        "example_sha256s": dataset.example_sha256s,
        "dataset_sha256": dataset.dataset_sha256,
        "feature_names": FEATURE_NAMES,
        "timeframe_shapes": {name: [count, len(FEATURE_NAMES)]
                             for name, count in WINDOW_BARS.items()},
        "future_raw_bars_included": False, "automatic_promotion": False,
        "position_permission": False, "order_permission": False}


def dataset_protocol_manifest() -> dict[str, Any]:
    payload = {"protocol": PROTOCOL, "feature_names": FEATURE_NAMES,
        "timeframe_shapes": {name: [count, len(FEATURE_NAMES)]
                             for name, count in WINDOW_BARS.items()},
        "future_label_bars": FUTURE_BAR_COUNT,
        "normalization": "CAUSAL_WINDOW_VOLUME_MEDIAN_ONLY",
        "future_raw_bars_included": False,
        "allowed_partition_purpose": {
            "TRAIN": "STAGE1_TRAIN", "DEVELOPMENT": "DEVELOPMENT_EVAL",
            "SEALED_TEST": "FINAL_SEALED_EVAL"},
        "automatic_promotion": False, "position_permission": False,
        "order_permission": False}
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
