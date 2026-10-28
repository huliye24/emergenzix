"""Acceptance tests for the opt-in weekly UP-UP SMA A/B comparison."""
from datetime import datetime, timedelta, timezone

from emergenzix_api.backtesting import run_sma_crossover
from emergenzix_api.schemas import Bar
from emergenzix_api.weekly_regime_ab import compare_sma_up_up


START = datetime(2025, 1, 6, tzinfo=timezone.utc)


def _bars(prices):
    return [
        Bar(
            timestamp=START + timedelta(hours=4 * index),
            open=price, high=price, low=price, close=price, volume=100,
        )
        for index, price in enumerate(prices)
    ]


def test_default_sma_result_is_unchanged_when_gate_is_not_supplied():
    bars = _bars([10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11])
    common = dict(
        fast_window=2, slow_window=3, initial_cash=1000,
        fee_bps=10, slippage_bps=5,
    )
    original = run_sma_crossover(bars, **common)
    explicit = run_sma_crossover(bars, entry_gate=None, **common)
    assert original == explicit
    assert original["blocked_entries_count"] == 0


def test_entry_gate_blocks_entries_but_never_changes_exit_logic():
    bars = _bars([10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11])
    common = dict(
        fast_window=2, slow_window=3, initial_cash=1000,
        fee_bps=10, slippage_bps=5,
    )
    allowed = run_sma_crossover(bars, entry_gate=lambda *_: True, **common)
    blocked = run_sma_crossover(bars, entry_gate=lambda *_: False, **common)
    assert allowed["number_of_trades"] > 0
    assert blocked["number_of_trades"] == 0
    assert blocked["blocked_entries_count"] == blocked["entry_signals_count"]
    assert allowed["trades"][0]["exit_timestamp"] is not None


def test_ab_uses_identical_inputs_and_reports_blocked_no_trade_window():
    weekly = [120, 115, 110, 105, 100, 95]
    prices = [price for price in weekly for _ in range(42)]
    prices.extend([95, 94, 93, 92, 91, 90, 91, 92, 93, 94, 95, 96])
    result = compare_sma_up_up(
        _bars(prices), fast_window=2, slow_window=5,
        initial_cash=1000, fee_bps=10, slippage_bps=5,
        evaluation_start_index=252,
    )
    assert result["same_bars_costs_and_execution"] is True
    assert result["exits_filtered"] is False
    assert result["orders_created"] is False
    assert result["baseline"]["number_of_trades"] == 1
    assert result["up_up_entry_gate"]["blocked_entries_count"] == 1
    assert result["up_up_entry_gate"]["no_trade_window"] is True
    assert result["up_up_entry_gate"]["total_fees"] == 0
