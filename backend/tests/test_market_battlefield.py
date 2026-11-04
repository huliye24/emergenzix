import pytest

from emergenzix_api.market_battlefield import build_market_battlefield


def _snapshot():
    return {
        "observed_at": "2026-11-04T04:00:00+00:00", "symbol": "BTCUSDT",
        "bids": [[99.9, 10], [99.5, 40], [99.0, 5]],
        "asks": [[100.1, 5], [100.5, 10], [101.0, 10]],
        "trades": [
            {"price": 100.0, "quantity": 8, "aggressor_side": "BUY"},
            {"price": 99.9, "quantity": 2, "aggressor_side": "SELL"},
        ],
        "derivatives": {"open_interest": 1100, "open_interest_previous": 1000,
                        "funding_rate": 0.0001},
    }


def test_builds_descriptive_flow_liquidity_and_derivatives_state():
    result = build_market_battlefield(_snapshot())
    assert result["flow"]["direction"] == "BUY"
    assert result["liquidity"]["pressure"] == "BID"
    assert result["liquidity"]["support"]["price"] == 99.5
    assert result["liquidity"]["resistance"]["verification"] == "VISIBLE_SNAPSHOT_ONLY"
    assert result["derivatives"]["open_interest_change"] == pytest.approx(0.1)
    assert result["state"]["flow_depth_agreement"] == "CONFIRMED"
    assert result["order_permission"] is False


def test_conflicting_flow_and_depth_are_not_presented_as_confirmation():
    snapshot = _snapshot()
    snapshot["bids"] = [[99.9, 1], [99.5, 1], [99.0, 1]]
    snapshot["asks"] = [[100.1, 30], [100.5, 20], [101.0, 10]]
    result = build_market_battlefield(snapshot)
    assert result["flow"]["direction"] == "BUY"
    assert result["liquidity"]["pressure"] == "ASK"
    assert result["state"]["flow_depth_agreement"] == "CONFLICT"
    assert result["state"]["label"] == "BUY_FLOW_FACING_VISIBLE_RESISTANCE"
