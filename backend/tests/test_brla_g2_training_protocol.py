from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.brla_g2_training_protocol import (
    DataPurpose, OrderState, Stage1Label, Stage1Observation, Stage1Prediction,
    Stage2Observation, authorize_data_use, protocol_manifest, stage1_loss,
    stage2_reward,
)
from emergenzix_api.brmg_scenarios import Partition

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)
HASHES = {timeframe: character * 64 for timeframe, character in
          zip(("15m", "1h", "4h", "1d"), "abcd", strict=True)}


def prediction():
    probabilities = {state: .05 for state in OrderState}
    probabilities[OrderState.OLD_ORDER_DOMINANT] = .75
    return Stage1Prediction(.8, .2, probabilities, .7)


def test_future_data_is_allowed_only_in_stage1_label():
    observation = Stage1Observation("e" * 64, NOW, NOW, HASHES, Partition.TRAIN)
    label = Stage1Label(NOW, NOW + timedelta(days=2), .9, .1,
                        OrderState.OLD_ORDER_DOMINANT, .8)
    assert observation.feature_end <= observation.as_of < label.label_horizon_end
    with pytest.raises(ValueError, match="future features"):
        Stage1Observation("e" * 64, NOW, NOW + timedelta(minutes=1),
                          HASHES, Partition.TRAIN)


def test_stage_one_is_descriptive_and_has_no_position_authority():
    result = prediction()
    assert result.position_permission is False and result.order_permission is False
    with pytest.raises(ValueError, match="no position or order authority"):
        Stage1Prediction(.8, .2, result.state_probabilities, .7,
                         position_permission=True)


def test_stage_two_consumes_prediction_fingerprint_not_future_label():
    state = Stage2Observation("a" * 64, "b" * 64, -.1, .02, .01, Partition.TRAIN)
    assert not hasattr(state, "label") and not hasattr(state, "future_outcome")
    with pytest.raises(TypeError):
        Stage2Observation("a" * 64, "b" * 64, -.1, .02, .01,
                          Partition.TRAIN, future_label="leak")


def test_losses_and_rewards_are_component_auditable_and_deterministic():
    label = Stage1Label(NOW, NOW + timedelta(days=2), .9, .1,
                        OrderState.OLD_ORDER_DOMINANT, .8)
    first = stage1_loss(prediction(), label)
    second = stage1_loss(prediction(), label)
    assert first == second and first.total == pytest.approx(sum(first.components.values()))
    reward = stage2_reward(gross_pnl=.01, transaction_cost=.001,
        drawdown_increase=.002, absolute_exposure=.1, unsupported_trade=False,
        contest_exposure=False, old_direction_after_new_order=False,
        delayed_exit=False, correct_wait=False, timely_reduce=True,
        timely_exit=False, risk_released_add=False, proposed_target=-.25,
        brpm_target=-.1, brgc_target=-.1, executed_target=-.1)
    assert reward.total == pytest.approx(sum(reward.components.values()))
    assert len(reward.audit_sha256) == 64


def test_stage_two_cannot_bypass_brpm_or_brgc():
    common = dict(gross_pnl=0, transaction_cost=0, drawdown_increase=0,
        absolute_exposure=.1, unsupported_trade=False, contest_exposure=False,
        old_direction_after_new_order=False, delayed_exit=False,
        correct_wait=True, timely_reduce=False, timely_exit=False,
        risk_released_add=False, proposed_target=-.5, brpm_target=-.1,
        brgc_target=-.1)
    with pytest.raises(PermissionError, match="BRGC-approved"):
        stage2_reward(**common, executed_target=-.5)
    with pytest.raises(PermissionError, match="authority chain"):
        stage2_reward(**{**common, "brpm_target": 0}, executed_target=-.1)


def test_data_partitions_are_strictly_separated():
    authorize_data_use(Partition.TRAIN, DataPurpose.STAGE1_TRAIN)
    authorize_data_use(Partition.TRAIN, DataPurpose.STAGE2_TRAIN)
    authorize_data_use(Partition.DEVELOPMENT, DataPurpose.DEVELOPMENT_EVAL)
    with pytest.raises(PermissionError):
        authorize_data_use(Partition.DEVELOPMENT, DataPurpose.STAGE2_TRAIN)
    with pytest.raises(PermissionError):
        authorize_data_use(Partition.SEALED_TEST, DataPurpose.FINAL_SEALED_EVAL)
    authorize_data_use(Partition.SEALED_TEST, DataPurpose.FINAL_SEALED_EVAL,
                       model_frozen=True, sealed_access_authorized=True)


def test_protocol_is_frozen_and_hashed():
    manifest = protocol_manifest()
    assert manifest["stage1_position_permission"] is False
    assert manifest["stage2_requires_brpm"] is True
    assert len(manifest["protocol_sha256"]) == 64
