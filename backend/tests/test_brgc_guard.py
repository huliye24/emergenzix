from emergenzix_api.brgc_guard import gate_paper_target


def test_guard_fails_flat_on_unknown_or_unbounded_state():
    stale = gate_paper_target(-.1, market_data_fresh=False,
        account_state_certain=True, risk_state_valid=True)
    oversized = gate_paper_target(-.5, hard_maximum_short_fraction=.1,
        market_data_fresh=True, account_state_certain=True, risk_state_valid=True)
    assert stale.approved_paper_target_fraction == 0
    assert oversized.approved_paper_target_fraction == 0
    assert stale.order_permission is False and oversized.order_permission is False


def test_guard_only_approves_paper_target_not_an_order():
    result = gate_paper_target(-.1, market_data_fresh=True,
        account_state_certain=True, risk_state_valid=True)
    assert result.decision == "ALLOW_PAPER_TARGET"
    assert result.approved_paper_target_fraction == -.1
    assert result.mode == "PAPER" and result.order_permission is False
