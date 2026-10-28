"""Offline A/B comparison for the causal weekly UP-UP SMA entry gate.

This module is research-only. It does not persist records, change the default
backtest, promote a strategy, or create orders.
"""
from __future__ import annotations

from typing import Any, Sequence

from .backtesting import run_sma_crossover
from .schemas import Bar
from .weekly_regime_filter import up_up_entry_allowed


def _metrics(run: dict[str, Any]) -> dict[str, Any]:
    trades = run["trades"]
    total_fees = sum(
        float(trade["entry_fee"]) + float(trade["exit_fee"]) for trade in trades
    )
    bars_count = int(run["bars_count"])
    return {
        "total_return_pct": float(run["total_return_pct"]),
        "max_drawdown_pct": float(run["max_drawdown_pct"]),
        "number_of_trades": int(run["number_of_trades"]),
        "exposure_pct": round(
            int(run["exposed_bars_count"]) / bars_count * 100.0, 6
        ) if bars_count else 0.0,
        "total_fees": round(total_fees, 8),
        "entry_signals_count": int(run["entry_signals_count"]),
        "blocked_entries_count": int(run["blocked_entries_count"]),
        "no_trade_window": int(run["number_of_trades"]) == 0,
    }


def compare_sma_up_up(
    bars: Sequence[Bar], *, fast_window: int, slow_window: int,
    initial_cash: float, fee_bps: float, slippage_bps: float,
    evaluation_start_index: int = 0,
) -> dict[str, Any]:
    """Compare one frozen SMA baseline with and without the UP-UP entry gate."""
    source = list(bars)
    common = {
        "fast_window": fast_window,
        "slow_window": slow_window,
        "initial_cash": initial_cash,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "evaluation_start_index": evaluation_start_index,
    }
    baseline = run_sma_crossover(source, **common)
    filtered = run_sma_crossover(
        source,
        entry_gate=lambda history, as_of: up_up_entry_allowed(
            history, as_of=as_of
        ),
        **common,
    )
    return {
        "comparison_version": "weekly-up-up-sma-ab-v1",
        "configuration": {
            "fast_window": fast_window,
            "slow_window": slow_window,
            "initial_cash": initial_cash,
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "evaluation_start_index": evaluation_start_index,
        },
        "baseline": _metrics(baseline),
        "up_up_entry_gate": _metrics(filtered),
        "same_bars_costs_and_execution": True,
        "exits_filtered": False,
        "automatic_promotion": False,
        "orders_created": False,
    }
