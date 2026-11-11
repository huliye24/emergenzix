import pytest

from emergenzix_api.brla_g2_ppo_fine_tuning import (
    authorize_warm_start, fine_tuning_chunks,
)


def qualified_report():
    return {
        "protocol": "emergenzix-brla-g2-behavioral-warm-start-v3",
        "decision": "READY_FOR_PPO_FINE_TUNING",
        "model_file_sha256": "a" * 64,
        "mode": "PAPER",
        "automatic_promotion": False,
        "position_permission": False,
        "order_permission": False,
    }


def test_qualified_warm_start_is_authorized_for_ppo_fine_tuning():
    authorize_warm_start(qualified_report(), "a" * 64)


@pytest.mark.parametrize("field,value", [
    ("decision", "WARM_START_NOT_READY"),
    ("model_file_sha256", "b" * 64),
    ("mode", "LIVE"),
    ("order_permission", True),
])
def test_unqualified_or_unsafe_warm_start_fails_closed(field, value):
    report = qualified_report(); report[field] = value
    with pytest.raises(PermissionError):
        authorize_warm_start(report, "a" * 64)


def test_fine_tuning_chunks_preserve_exact_budget():
    assert fine_tuning_chunks(12_500, 5_000) == (5_000, 5_000, 2_500)
    assert sum(fine_tuning_chunks(50_000, 5_000)) == 50_000


def test_fine_tuning_chunks_reject_non_positive_values():
    with pytest.raises(ValueError, match="positive"):
        fine_tuning_chunks(50_000, 0)
