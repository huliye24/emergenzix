import math

from emergenzix_api.position_management import (
    LongPositionManagementRequest,
    plan_long_position_management,
)


def _request(**overrides):
    values = {
        "strategy_version_id": "breakout-v1",
        "position_id": "paper-position-1",
        "current_price": 120.0,
        "high_watermark": 125.0,
        "average_entry": 100.0,
        "initial_stop": 90.0,
        "current_stop": 105.0,
        "atr": 5.0,
        "atr_multiple": 2.0,
        "current_quantity": 10.0,
    }
    values.update(overrides)
    return LongPositionManagementRequest(**values)


def test_stop_tightens_from_high_watermark_and_never_creates_order():
    plan = plan_long_position_management(_request())
    assert plan.status == "approved_for_review"
    assert plan.proposed_stop == 115.0
    assert plan.stop_changed is True
    assert plan.creates_order is False


def test_stop_never_widens_when_atr_candidate_is_lower():
    plan = plan_long_position_management(
        _request(current_price=112.0, high_watermark=113.0, current_stop=108.0, atr=4.0)
    )
    assert plan.proposed_stop == 108.0
    assert plan.stop_changed is False


def test_break_even_floor_activates_only_after_frozen_r_threshold():
    before = plan_long_position_management(
        _request(current_price=109.0, high_watermark=109.0, current_stop=95.0, atr=10.0)
    )
    after = plan_long_position_management(
        _request(current_price=110.0, high_watermark=110.0, current_stop=95.0, atr=10.0)
    )
    assert before.proposed_stop == 95.0
    assert after.proposed_stop == 100.0


def test_only_next_partial_exit_is_proposed():
    first = plan_long_position_management(_request(current_price=125.0))
    second = plan_long_position_management(
        _request(
            current_price=135.0,
            high_watermark=135.0,
            completed_partial_exits=1,
            current_quantity=7.5,
        )
    )
    assert first.partial_exit_index == 0
    assert first.partial_exit_quantity == 2.5
    assert second.partial_exit_index == 1
    assert second.partial_exit_quantity == 1.875


def test_crossed_stop_requires_exit_and_does_not_move_stop():
    plan = plan_long_position_management(
        _request(current_price=104.0, high_watermark=125.0, current_stop=105.0)
    )
    assert plan.status == "exit_required"
    assert plan.proposed_stop == 105.0
    assert plan.creates_order is False


def test_invalid_or_non_finite_inputs_fail_closed():
    invalid = plan_long_position_management(_request(current_stop=89.0))
    non_finite = plan_long_position_management(_request(atr=math.nan))
    assert invalid.status == "rejected"
    assert non_finite.status == "rejected"
