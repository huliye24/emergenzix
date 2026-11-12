from dataclasses import replace

import pytest

from emergenzix_api.brla_g2_trend_timing import (
    TrendTimingPrediction, build_stage1_timing_dataset, stage2_timing_reward,
    timing_loss, timing_protocol_manifest,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose
from emergenzix_api.brmg_scenarios import Partition
from emergenzix_api.brpm_g1 import BRPMRequest, allocate_short_risk
from test_brla_g2_stage1_dataset import bars


def base_reward():
    return dict(gross_pnl=.01, transaction_cost=.001, drawdown_increase=.001,
        absolute_exposure=.1, unsupported_trade=False, contest_exposure=False,
        old_direction_after_new_order=False, delayed_exit=False, correct_wait=False,
        timely_reduce=False, timely_exit=False, risk_released_add=False,
        proposed_target=-.1, brpm_target=-.1, brgc_target=-.1, executed_target=-.1)


@pytest.fixture(scope="module")
def timing_dataset():
    return build_stage1_timing_dataset(bars(), scenario_sha256="a" * 64,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE1_TRAIN,
        stride_bars=768)


def test_v2_dataset_adds_timing_targets_without_future_raw_bars(timing_dataset):
    example = timing_dataset.examples[0]
    label = example.timing_label
    assert all(0 <= value <= 1 for value in (label.trend_maturity,
        label.remaining_potential, label.entry_location_quality,
        label.feedback_exhaustion, label.late_entry_risk))
    assert example.future_raw_bars_included is False
    assert not hasattr(example, "future_bars")
    assert len(example.example_sha256) == 64


def test_timing_loss_is_separate_and_auditable(timing_dataset):
    label = timing_dataset.examples[0].timing_label
    prediction = TrendTimingPrediction(label.trend_maturity, label.remaining_potential,
        label.entry_location_quality, label.feedback_exhaustion, label.late_entry_risk)
    audit = timing_loss(prediction, label)
    assert audit.total == pytest.approx(0)
    assert set(audit.components) == {"trend_maturity_mse", "remaining_potential_mse",
        "entry_location_mse", "feedback_exhaustion_mse", "late_entry_risk_mse"}


def test_stage_two_distinguishes_late_entry_from_profitable_hold():
    late_entry = stage2_timing_reward(trend_maturity=.8, remaining_potential=.2,
        entry_location_quality=.1, feedback_exhaustion=.8, late_entry_risk=.9,
        new_entry=True, hold_existing=False, add_risk=False, reduce=False,
        exit_position=False, unrealized_profit=0, old_order_invalidated=False,
        stop_tightened=False, **base_reward())
    hold = stage2_timing_reward(trend_maturity=.8, remaining_potential=.2,
        entry_location_quality=.1, feedback_exhaustion=.8, late_entry_risk=.9,
        new_entry=False, hold_existing=True, add_risk=False, reduce=False,
        exit_position=False, unrealized_profit=.1, old_order_invalidated=False,
        stop_tightened=True, **base_reward())
    assert late_entry.components["late_new_entry"] < 0
    assert hold.components["profitable_mature_hold"] > 0
    assert hold.components["protective_stop_tightening"] > 0
    assert hold.total > late_entry.total


def test_brpm_hard_blocks_new_risk_in_mature_trend_but_allows_exit():
    mature_probe = BRPMRequest("btc", 10_000, 100, 0, -.1, 110,
                              trend_maturity=.8, entry_location_quality=.8)
    rejected = allocate_short_risk(mature_probe)
    assert rejected.reason == "LATE_ENTRY_FORBIDDEN"
    early = allocate_short_risk(replace(mature_probe, trend_maturity=.3))
    mature_add = allocate_short_risk(BRPMRequest("btc", 10_000, 90, -.1, -.2, 94,
        trend_maturity=.8, entry_location_quality=.8), [early])
    assert mature_add.reason == "MATURE_TREND_ADD_FORBIDDEN"
    reduced = allocate_short_risk(BRPMRequest("btc", 10_000, 90, -.1, -.05, 94,
        trend_maturity=.95, entry_location_quality=0), [early])
    assert reduced.decision == "APPROVE_REDUCE"
    assert reduced.final_worst_loss_fraction < early.final_worst_loss_fraction
    exit_entry = allocate_short_risk(BRPMRequest("btc", 10_000, 90, -.1, 0, None,
        trend_maturity=.95, entry_location_quality=0), [early])
    assert exit_entry.decision == "APPROVE_EXIT"


def test_timing_protocol_creates_a_new_version():
    manifest = timing_protocol_manifest()
    assert manifest["protocol"] == "emergenzix-brla-g2-trend-timing-v2"
    assert manifest["mature_new_risk_threshold"] == .70
    assert len(manifest["protocol_sha256"]) == 64
