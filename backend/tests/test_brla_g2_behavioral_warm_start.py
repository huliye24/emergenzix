import pytest

from emergenzix_api.brla_g2_behavioral_warm_start import (
    PROTOCOL, calibrated_class_weights, classification_audit,
)
from emergenzix_api.brla_g2_ppo_env import INTENTS


def test_classification_audit_reports_recall_diversity_and_legality():
    audit = classification_audit(
        labels=[0, 1, 2, 5], predictions=[0, 1, 2, 2],
        masks=[[1, 1, 0, 0, 0, 0], [1, 1, 0, 0, 0, 0],
               [0, 0, 1, 1, 0, 1], [0, 0, 1, 1, 0, 1]])
    assert audit["accuracy"] == .75
    assert audit["macro_recall"] == .75
    assert audit["predicted_action_diversity"] == 3
    assert audit["illegal_prediction_count"] == 0
    assert audit["predicted_probe_fraction"] == .25


def test_classification_audit_rejects_mismatched_inputs():
    with pytest.raises(ValueError, match="same non-zero length"):
        classification_audit([0], [], [[True]])


def test_calibrated_weights_reduce_rare_probe_pressure_and_boost_exit():
    weights = calibrated_class_weights({0: 1243, 1: 281, 2: 355, 5: 281})
    assert weights[1] / weights[0] < 3
    assert weights[INTENTS.index("EXIT")] > weights[INTENTS.index("PROBE_SHORT")]
    assert sum(weights.values()) / len(weights) == pytest.approx(1)


def test_calibrated_weights_reject_invalid_counts():
    with pytest.raises(ValueError, match="valid actions"):
        calibrated_class_weights({0: 0})


def test_warm_start_protocol_tracks_causal_curriculum_generation():
    assert PROTOCOL == "emergenzix-brla-g2-behavioral-warm-start-v3"
