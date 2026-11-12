"""Auditable readiness checks for BRLA-G2 stage-one datasets."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Sequence

from .brla_g2_order_labels import CHALLENGER_FLOOR
from .brla_g2_stage1_dataset import Stage1Dataset, dataset_manifest
from .brla_g2_training_protocol import DataPurpose, OrderState
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-stage1-dataset-audit-v1"
MIN_EXAMPLES_PER_PARTITION = 30
MAX_IMBALANCE_RATIO = 10.0
MAX_SATURATION_FRACTION = .35
MAX_FEATURE_OVERLAP = .97
MAX_LABEL_OVERLAP = 0.0


def _adjacent_overlap(examples, window: timedelta) -> float:
    times = sorted(datetime.fromisoformat(example.as_of) for example in examples)
    if len(times) < 2:
        return 0.0
    return max(max(0.0, 1 - (right - left) / window)
               for left, right in zip(times, times[1:]))


def audit_stage1_datasets(datasets: Sequence[Stage1Dataset], *,
                          source_sha256: str,
                          source_start: str,
                          source_end: str) -> dict[str, Any]:
    if len(source_sha256) != 64 or not datasets:
        raise ValueError("source fingerprint and at least one dataset are required")
    examples = [example for dataset in datasets for example in dataset.examples]
    if not examples:
        raise ValueError("datasets contain no examples")
    counts = Counter(example.label.state.value for example in examples)
    all_counts = {state.value: counts.get(state.value, 0) for state in OrderState}
    nonzero = [count for count in all_counts.values() if count]
    imbalance = max(nonzero) / min(nonzero) if nonzero else float("inf")
    total = len(examples)
    old_values = [example.label.old_order_strength for example in examples]
    new_values = [example.label.new_order_strength for example in examples]
    state_fractions = {state: count / total for state, count in all_counts.items()}
    partition_counts = {partition.value: sum(
        len(dataset.examples) for dataset in datasets if dataset.partition is partition)
        for partition in Partition}
    partition_scenarios = {partition.value: sorted({
        dataset.scenario_sha256 for dataset in datasets if dataset.partition is partition})
        for partition in Partition}
    scenario_sets = [set(values) for values in partition_scenarios.values()]
    partitions_disjoint = all(scenario_sets[index].isdisjoint(scenario_sets[other])
        for index in range(3) for other in range(index + 1, 3))
    purpose_matches = all(
        (dataset.partition is Partition.TRAIN and dataset.purpose is DataPurpose.STAGE1_TRAIN)
        or (dataset.partition is Partition.DEVELOPMENT
            and dataset.purpose is DataPurpose.DEVELOPMENT_EVAL)
        or (dataset.partition is Partition.SEALED_TEST
            and dataset.purpose is DataPurpose.FINAL_SEALED_EVAL)
        for dataset in datasets)
    feature_overlap = max((_adjacent_overlap(dataset.examples, timedelta(days=180))
                           for dataset in datasets), default=0.0)
    label_overlap = max((_adjacent_overlap(dataset.examples, timedelta(days=7))
                         for dataset in datasets), default=0.0)
    saturation = {
        "old_at_or_above_0_98": sum(value >= .98 for value in old_values) / total,
        "old_at_or_below_0_02": sum(value <= .02 for value in old_values) / total,
        "new_at_or_above_0_98": sum(value >= .98 for value in new_values) / total,
        "new_at_challenger_floor": sum(value <= CHALLENGER_FLOOR + 1e-12
                                        for value in new_values) / total,
    }
    partition_quality = {}
    for partition in Partition:
        selected = [example for example in examples if example.partition is partition]
        selected_counts = Counter(example.label.state.value for example in selected)
        nonzero_counts = [selected_counts[state.value] for state in OrderState
                          if selected_counts[state.value] > 0]
        old = [example.label.old_order_strength for example in selected]
        new = [example.label.new_order_strength for example in selected]
        partition_quality[partition.value] = {
            "all_six_states_present": bool(selected)
                and all(selected_counts[state.value] > 0 for state in OrderState),
            "imbalance_ratio": (max(nonzero_counts) / min(nonzero_counts)
                                if nonzero_counts else None),
            "maximum_saturation_fraction": (max(
                sum(value >= .98 for value in old) / len(selected),
                sum(value <= .02 for value in old) / len(selected),
                sum(value >= .98 for value in new) / len(selected),
                sum(value <= CHALLENGER_FLOOR + 1e-12 for value in new) / len(selected),
            ) if selected else None),
        }
    checks = {
        "all_six_states_present": all(
            row["all_six_states_present"] for row in partition_quality.values()
            if row["imbalance_ratio"] is not None),
        "imbalance_within_limit": all(
            row["imbalance_ratio"] is not None
            and row["imbalance_ratio"] <= MAX_IMBALANCE_RATIO
            and row["all_six_states_present"] for row in partition_quality.values()
            if row["imbalance_ratio"] is not None),
        "strength_saturation_within_limit": all(
            row["maximum_saturation_fraction"] <= MAX_SATURATION_FRACTION
            for row in partition_quality.values()
            if row["maximum_saturation_fraction"] is not None),
        "feature_overlap_within_limit": feature_overlap <= MAX_FEATURE_OVERLAP,
        "label_overlap_within_limit": label_overlap <= MAX_LABEL_OVERLAP,
        "all_partitions_present": all(partition_counts[p.value] > 0 for p in Partition),
        "minimum_examples_per_partition": all(
            partition_counts[p.value] >= MIN_EXAMPLES_PER_PARTITION for p in Partition),
        "partition_scenarios_disjoint": partitions_disjoint,
        "partition_purpose_matches": purpose_matches,
    }
    report = {"protocol": PROTOCOL, "source_sha256": source_sha256,
        "source_start": source_start, "source_end": source_end,
        "dataset_manifests": [dataset_manifest(dataset) for dataset in datasets],
        "example_count": total, "state_counts": all_counts,
        "state_fractions": state_fractions, "imbalance_ratio": imbalance,
        "old_strength": {"mean": statistics.fmean(old_values),
                         "minimum": min(old_values), "maximum": max(old_values)},
        "new_strength": {"mean": statistics.fmean(new_values),
                         "minimum": min(new_values), "maximum": max(new_values)},
        "saturation": saturation, "maximum_adjacent_feature_overlap": feature_overlap,
        "maximum_adjacent_label_overlap": label_overlap,
        "partition_quality": partition_quality,
        "partition_counts": partition_counts,
        "partition_scenarios": partition_scenarios,
        "checks": checks,
        "decision": "READY_FOR_ENCODER_TRAINING" if all(checks.values())
                    else "NOT_READY_FOR_ENCODER_TRAINING",
        "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return report
