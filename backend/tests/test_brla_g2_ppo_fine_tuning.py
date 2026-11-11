import pytest

from emergenzix_api.brla_g2_ppo_fine_tuning import authorize_warm_start


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
