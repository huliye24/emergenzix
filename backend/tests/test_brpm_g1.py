from dataclasses import replace

import pytest

from emergenzix_api.brpm_g1 import BRPMRequest, allocate_short_risk, verify_risk_ledger


def request(**changes):
    base = BRPMRequest(campaign_id="btc-bear-1", equity=10_000, mark_price=100,
        current_target_fraction=0, requested_target_fraction=-.10,
        protective_stop_price=110, maximum_campaign_risk_fraction=.02)
    return replace(base, **changes)


def test_probe_requires_stop_and_fits_shared_budget():
    missing = allocate_short_risk(request(protective_stop_price=None))
    approved = allocate_short_risk(request(protective_stop_price=110))
    assert missing.decision == "REJECT" and missing.reason == "VALID_SHORT_STOP_REQUIRED"
    assert approved.decision == "APPROVE_PROBE"
    assert approved.final_worst_loss_fraction == pytest.approx(.0103)
    assert approved.order_permission is False and approved.mode == "PAPER"


def test_add_is_rejected_when_stop_does_not_tighten_or_total_risk_does_not_fall():
    first = allocate_short_risk(request(protective_stop_price=110))
    no_move = allocate_short_risk(request(current_target_fraction=-.10,
        requested_target_fraction=-.20, protective_stop_price=110), [first])
    insufficient = allocate_short_risk(request(current_target_fraction=-.10,
        requested_target_fraction=-.20, protective_stop_price=105), [first])
    assert no_move.reason == "STOP_NOT_TIGHTENED"
    assert insufficient.reason == "TOTAL_RISK_NOT_LOWER_AFTER_ADD"
    assert no_move.approved_target_fraction == -.10
    assert insufficient.approved_target_fraction == -.10


def test_tightened_stop_releases_enough_risk_for_lower_risk_add():
    first = allocate_short_risk(request(protective_stop_price=110))
    second_request = request(mark_price=90, current_target_fraction=-.10,
        requested_target_fraction=-.20, protective_stop_price=94)
    second = allocate_short_risk(second_request, [first])
    assert second.decision == "APPROVE_ADD"
    assert second.released_risk_fraction > 0
    assert second.final_worst_loss_fraction < first.final_worst_loss_fraction
    assert verify_risk_ledger([first, second])


def test_portfolio_budget_includes_other_positions():
    result = allocate_short_risk(request(existing_other_positions_risk_fraction=.055,
        maximum_portfolio_risk_fraction=.06))
    assert result.decision == "REJECT"
    assert result.reason == "PORTFOLIO_RISK_BUDGET_EXCEEDED"
    assert result.portfolio_worst_loss_fraction == 0.055


def test_tampered_or_mismatched_ledger_fails_closed():
    first = allocate_short_risk(request())
    tampered = replace(first, final_worst_loss_fraction=0)
    result = allocate_short_risk(request(mark_price=90, current_target_fraction=-.10,
        requested_target_fraction=-.20, protective_stop_price=94), [tampered])
    assert result.decision == "REJECT"
    assert result.reason == "INVALID_OR_MISMATCHED_LEDGER"


def test_exit_remains_allowed_without_a_stop():
    first = allocate_short_risk(request())
    exit_entry = allocate_short_risk(request(current_target_fraction=-.10,
        requested_target_fraction=0, protective_stop_price=None), [first])
    assert exit_entry.decision == "APPROVE_EXIT"
    assert exit_entry.approved_target_fraction == 0
    assert exit_entry.final_worst_loss_fraction == 0
    assert verify_risk_ledger([first, exit_entry])
