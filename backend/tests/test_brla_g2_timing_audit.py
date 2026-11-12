from dataclasses import replace

from emergenzix_api.brla_g2_timing_audit import audit_timing_datasets
from emergenzix_api.brla_g2_trend_timing import (
    TimingStage1Dataset, TimingStage1Example, TrendTimingLabel,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose
from emergenzix_api.brmg_scenarios import Partition
from test_brla_g2_dataset_audit import dataset as base_dataset


def timing_dataset(*, saturated=False, mature_dominant=False):
    base = base_dataset(Partition.TRAIN, DataPurpose.STAGE1_TRAIN, "1" * 64)
    rows = []
    maturity_values = [.1, .3, .6, .77, .9] * 6
    if mature_dominant:
        maturity_values = [.8] * 24 + [.3] * 6
    for index, (example, maturity) in enumerate(zip(base.examples, maturity_values, strict=True)):
        timing = TrendTimingLabel(maturity, .01 if saturated else .6,
            .01 if saturated else .55, .4, .5, maturity, .5, f"{index:064x}")
        rows.append(TimingStage1Example(example, timing, f"{index + 200:064x}"))
    return TimingStage1Dataset("timing-v2", base.dataset_sha256, Partition.TRAIN,
        DataPurpose.STAGE1_TRAIN, tuple(rows), "f" * 64)


def base_ready(ready=True):
    return {"decision": "READY_FOR_ENCODER_TRAINING" if ready
            else "NOT_READY_FOR_ENCODER_TRAINING", "report_sha256": "a" * 64}


def test_balanced_timing_labels_are_ready_when_base_is_ready():
    report = audit_timing_datasets([timing_dataset()], base_audit=base_ready(),
                                   source_sha256="b" * 64)
    assert report["decision"] == "READY_FOR_TIMING_ENCODER_TRAINING"
    assert all(report["checks"].values())


def test_base_failure_blocks_timing_training():
    report = audit_timing_datasets([timing_dataset()], base_audit=base_ready(False),
                                   source_sha256="b" * 64)
    assert report["checks"]["base_dataset_ready"] is False
    assert report["decision"] == "NOT_READY_FOR_TIMING_ENCODER_TRAINING"


def test_saturated_labels_and_too_few_quality_entries_fail():
    report = audit_timing_datasets([timing_dataset(saturated=True)],
                                   base_audit=base_ready(), source_sha256="b" * 64)
    assert report["checks"]["timing_saturation_within_limit"] is False
    assert report["checks"]["high_quality_entries_sufficient"] is False


def test_mature_threshold_cannot_block_most_samples():
    report = audit_timing_datasets([timing_dataset(mature_dominant=True)],
                                   base_audit=base_ready(), source_sha256="b" * 64)
    assert report["mature_entry_block_fraction"] == .8
    assert report["checks"]["mature_entry_block_not_dominant"] is False
