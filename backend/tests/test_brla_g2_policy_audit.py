import pytest

from emergenzix_api.brla_g2_policy_audit import summarize_flat_probabilities


def test_probability_audit_distinguishes_collapse_from_near_tie():
    summary = summarize_flat_probabilities([
        {"wait_probability": .80, "probe_probability": .20},
        {"wait_probability": .52, "probe_probability": .48},
    ])
    assert summary["flat_decision_count"] == 2
    assert summary["probe_argmax_fraction"] == 0
    assert summary["decisive_wait_fraction"] == .5
    assert summary["near_tie_fraction"] == .5


def test_probability_audit_rejects_invalid_values():
    with pytest.raises(ValueError, match="finite"):
        summarize_flat_probabilities([
            {"wait_probability": 1.1, "probe_probability": 0},
        ])
