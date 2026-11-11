import pytest

from emergenzix_api.brla_g2_behavioral_warm_start import classification_audit


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
