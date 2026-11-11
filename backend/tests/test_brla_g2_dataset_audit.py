from dataclasses import replace
from datetime import datetime, timedelta, timezone

from emergenzix_api.brla_g2_dataset_audit import audit_stage1_datasets
from emergenzix_api.brla_g2_order_labels import CompetitionLabel, OldDirection
from emergenzix_api.brla_g2_stage1_dataset import Stage1Dataset, Stage1Example
from emergenzix_api.brla_g2_training_protocol import DataPurpose, OrderState
from emergenzix_api.brmg_scenarios import Partition

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def dataset(partition, purpose, scenario, *, spacing_days=7, saturated=False):
    examples = []
    states = list(OrderState)
    for index in range(30):
        state = states[index % len(states)]
        as_of = START + timedelta(days=index * spacing_days)
        old = .99 if saturated else .5 + (index % 3) * .05
        new = .45 - (index % 3) * .05
        label = CompetitionLabel("label-v1", scenario, partition, as_of.isoformat(),
            (as_of + timedelta(days=7)).isoformat(), OldDirection.BEARISH,
            old, new, new - old, .5, 0, .7, state, {}, "b" * 64,
            f"{index:064x}")
        examples.append(Stage1Example(scenario, partition, as_of.isoformat(),
            ("a",), {"15m": ((0.0,),)}, {"15m": "c" * 64}, "d" * 64,
            label, f"{index + 100:064x}"))
    hashes = tuple(example.example_sha256 for example in examples)
    return Stage1Dataset("dataset-v1", scenario, partition, purpose, 672,
                         tuple(examples), hashes, "e" * 64)


def complete():
    return [dataset(Partition.TRAIN, DataPurpose.STAGE1_TRAIN, "1" * 64),
        dataset(Partition.DEVELOPMENT, DataPurpose.DEVELOPMENT_EVAL, "2" * 64),
        dataset(Partition.SEALED_TEST, DataPurpose.FINAL_SEALED_EVAL, "3" * 64)]


def audit(datasets):
    return audit_stage1_datasets(datasets, source_sha256="a" * 64,
                                 source_start="2025-01-01T00:00:00+00:00",
                                 source_end="2026-12-31T00:00:00+00:00")


def test_balanced_disjoint_nonoverlapping_datasets_are_ready():
    report = audit(complete())
    assert report["decision"] == "READY_FOR_ENCODER_TRAINING"
    assert all(report["checks"].values())
    assert len(report["report_sha256"]) == 64


def test_missing_partitions_and_states_fail_readiness():
    only_train = dataset(Partition.TRAIN, DataPurpose.STAGE1_TRAIN, "1" * 64)
    shortened = replace(only_train, examples=only_train.examples[:5],
                        example_sha256s=only_train.example_sha256s[:5])
    report = audit([shortened])
    assert report["checks"]["all_partitions_present"] is False
    assert report["checks"]["all_six_states_present"] is False
    assert report["checks"]["strength_saturation_within_limit"] is True
    assert report["decision"] == "NOT_READY_FOR_ENCODER_TRAINING"


def test_saturation_and_overlapping_labels_fail_readiness():
    datasets = complete()
    datasets[0] = dataset(Partition.TRAIN, DataPurpose.STAGE1_TRAIN,
                          "1" * 64, spacing_days=1, saturated=True)
    report = audit(datasets)
    assert report["checks"]["strength_saturation_within_limit"] is False
    assert report["checks"]["label_overlap_within_limit"] is False
    assert report["decision"] == "NOT_READY_FOR_ENCODER_TRAINING"


def test_same_scenario_cannot_appear_across_partitions():
    datasets = complete()
    datasets[1] = dataset(Partition.DEVELOPMENT, DataPurpose.DEVELOPMENT_EVAL,
                          "1" * 64)
    report = audit(datasets)
    assert report["checks"]["partition_scenarios_disjoint"] is False
