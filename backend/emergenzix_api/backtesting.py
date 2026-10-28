"""Deterministic long-only research backtests; no exchange access."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .schemas import Bar


def _sma(values: list[float], window: int, end_index: int) -> float:
    start = end_index - window + 1
    return sum(values[start : end_index + 1]) / window


def run_sma_crossover(
    bars: list[Bar],
    *,
    fast_window: int,
    slow_window: int,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
    evaluation_start_index: int = 0,
    entry_gate: Callable[[list[Bar], datetime], bool] | None = None,
) -> dict[str, Any]:
    """Run a long-only, all-cash SMA crossover simulation.

    A signal is computed from a completed bar and executed at the next bar's
    open, reducing look-ahead bias. Open positions are closed at the final
    bar's close so final equity includes estimated fees and slippage.

    When evaluation_start_index is positive, earlier bars are indicator warm-up
    only: no position is opened before the evaluation boundary, the portfolio
    starts with initial_cash at the prior candle's close, and reported metrics
    cover only candles from the requested evaluation index onward.
    """
    if fast_window >= slow_window:
        raise ValueError("fast_window must be smaller than slow_window")
    if len(bars) < slow_window + 2:
        raise ValueError(f"at least {slow_window + 2} bars are required")
    if not 0 <= evaluation_start_index < len(bars):
        raise ValueError("evaluation_start_index must point to an existing bar")

    execution_start_index = max(1, evaluation_start_index)
    closes = [float(bar.close) for bar in bars]
    cash = float(initial_cash)
    quantity = 0.0
    entry_price = 0.0
    entry_fee = 0.0
    entry_timestamp: str | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [
        {
            "timestamp": bars[execution_start_index - 1].timestamp.isoformat(),
            "equity": round(cash, 8),
        }
    ]
    fee_rate = fee_bps / 10_000.0
    slippage_rate = slippage_bps / 10_000.0
    entry_signals_count = 0
    blocked_entries_count = 0
    exposed_bars_count = 0

    for execution_index in range(execution_start_index, len(bars)):
        signal_index = execution_index - 1

        if signal_index >= slow_window:
            previous_fast = _sma(closes, fast_window, signal_index - 1)
            previous_slow = _sma(closes, slow_window, signal_index - 1)
            current_fast = _sma(closes, fast_window, signal_index)
            current_slow = _sma(closes, slow_window, signal_index)
            cross_up = previous_fast <= previous_slow and current_fast > current_slow
            cross_down = previous_fast >= previous_slow and current_fast < current_slow
            execution_bar = bars[execution_index]

            if cross_up and quantity <= 0 and cash > 0:
                entry_signals_count += 1
                allowed = entry_gate is None or entry_gate(
                    bars[: signal_index + 1], execution_bar.timestamp
                )
                if not allowed:
                    blocked_entries_count += 1
                else:
                    entry_price = float(execution_bar.open) * (1.0 + slippage_rate)
                    quantity = cash / (entry_price * (1.0 + fee_rate))
                    notional = quantity * entry_price
                    entry_fee = notional * fee_rate
                    cash = 0.0
                    entry_timestamp = execution_bar.timestamp.isoformat()

            elif cross_down and quantity > 0:
                exit_price = float(execution_bar.open) * (1.0 - slippage_rate)
                notional = quantity * exit_price
                exit_fee = notional * fee_rate
                net_pnl = (notional - exit_fee) - (quantity * entry_price + entry_fee)
                cash += notional - exit_fee
                trades.append(
                    _trade_record(
                        entry_timestamp=entry_timestamp,
                        exit_timestamp=execution_bar.timestamp.isoformat(),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        entry_fee=entry_fee,
                        exit_fee=exit_fee,
                        net_pnl=net_pnl,
                    )
                )
                quantity = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                entry_timestamp = None

        if quantity > 0:
            exposed_bars_count += 1
        marked_equity = cash + quantity * float(bars[execution_index].close)
        equity_curve.append(
            {
                "timestamp": bars[execution_index].timestamp.isoformat(),
                "equity": round(marked_equity, 8),
            }
        )

    if quantity > 0:
        final_bar = bars[-1]
        exit_price = float(final_bar.close) * (1.0 - slippage_rate)
        notional = quantity * exit_price
        exit_fee = notional * fee_rate
        net_pnl = (notional - exit_fee) - (quantity * entry_price + entry_fee)
        cash += notional - exit_fee
        trades.append(
            _trade_record(
                entry_timestamp=entry_timestamp,
                exit_timestamp=final_bar.timestamp.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                net_pnl=net_pnl,
            )
        )
        quantity = 0.0
        equity_curve[-1]["equity"] = round(cash, 8)

    final_equity = cash
    peak = 0.0
    max_drawdown_pct = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak * 100.0
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

    completed_trades = len(trades)
    wins = sum(1 for trade in trades if trade["net_pnl"] > 0)
    first_close = closes[execution_start_index - 1]
    benchmark_return_pct = ((closes[-1] / first_close) - 1.0) * 100.0
    evaluated_bars_count = (
        len(bars)
        if evaluation_start_index == 0
        else len(bars) - evaluation_start_index
    )

    return {
        "bars_count": evaluated_bars_count,
        "initial_cash": round(initial_cash, 8),
        "final_equity": round(final_equity, 8),
        "total_return_pct": round((final_equity / initial_cash - 1.0) * 100.0, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "number_of_trades": completed_trades,
        "win_rate_pct": round((wins / completed_trades) * 100.0, 6)
        if completed_trades
        else 0.0,
        "benchmark_return_pct": round(benchmark_return_pct, 6),
        "entry_signals_count": entry_signals_count,
        "blocked_entries_count": blocked_entries_count,
        "exposed_bars_count": exposed_bars_count,
        "equity_curve": equity_curve,
        "trades": trades,
        "assumptions": {
            "execution": "signal at completed close; fill at next bar open",
            "positioning": "long-only, all available cash, no leverage",
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "open_position": "closed at final close if still open",
        },
    }


def run_donchian_breakout(
    bars: list[Bar],
    *,
    entry_window: int,
    exit_window: int,
    initial_cash: float,
    fee_bps: float,
    slippage_bps: float,
    evaluation_start_index: int = 0,
) -> dict[str, Any]:
    """Run a long-only Donchian breakout using prior completed candles only.

    A close above the preceding entry channel opens a position at the next bar's
    open. A close below the preceding exit channel closes it at the next open.
    The signal candle is excluded from both channel calculations.
    """
    if entry_window < 2 or exit_window < 2:
        raise ValueError("Donchian windows must be at least 2 bars")
    if exit_window >= entry_window:
        raise ValueError("exit_window must be smaller than entry_window")
    warmup = max(entry_window, exit_window)
    if len(bars) < warmup + 2:
        raise ValueError(f"at least {warmup + 2} bars are required")
    if not 0 <= evaluation_start_index < len(bars):
        raise ValueError("evaluation_start_index must point to an existing bar")

    execution_start_index = max(1, evaluation_start_index)
    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    cash = float(initial_cash)
    quantity = 0.0
    entry_price = 0.0
    entry_fee = 0.0
    entry_timestamp: str | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = [
        {
            "timestamp": bars[execution_start_index - 1].timestamp.isoformat(),
            "equity": round(cash, 8),
        }
    ]
    fee_rate = fee_bps / 10_000.0
    slippage_rate = slippage_bps / 10_000.0

    for execution_index in range(execution_start_index, len(bars)):
        signal_index = execution_index - 1
        if signal_index >= warmup:
            entry_channel = max(highs[signal_index - entry_window : signal_index])
            exit_channel = min(lows[signal_index - exit_window : signal_index])
            execution_bar = bars[execution_index]
            if closes[signal_index] > entry_channel and quantity <= 0 and cash > 0:
                entry_price = float(execution_bar.open) * (1.0 + slippage_rate)
                quantity = cash / (entry_price * (1.0 + fee_rate))
                notional = quantity * entry_price
                entry_fee = notional * fee_rate
                cash = 0.0
                entry_timestamp = execution_bar.timestamp.isoformat()
            elif closes[signal_index] < exit_channel and quantity > 0:
                exit_price = float(execution_bar.open) * (1.0 - slippage_rate)
                notional = quantity * exit_price
                exit_fee = notional * fee_rate
                net_pnl = (notional - exit_fee) - (quantity * entry_price + entry_fee)
                cash += notional - exit_fee
                trades.append(
                    _trade_record(
                        entry_timestamp=entry_timestamp,
                        exit_timestamp=execution_bar.timestamp.isoformat(),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        entry_fee=entry_fee,
                        exit_fee=exit_fee,
                        net_pnl=net_pnl,
                    )
                )
                quantity = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                entry_timestamp = None

        marked_equity = cash + quantity * closes[execution_index]
        equity_curve.append(
            {
                "timestamp": bars[execution_index].timestamp.isoformat(),
                "equity": round(marked_equity, 8),
            }
        )

    if quantity > 0:
        final_bar = bars[-1]
        exit_price = float(final_bar.close) * (1.0 - slippage_rate)
        notional = quantity * exit_price
        exit_fee = notional * fee_rate
        net_pnl = (notional - exit_fee) - (quantity * entry_price + entry_fee)
        cash += notional - exit_fee
        trades.append(
            _trade_record(
                entry_timestamp=entry_timestamp,
                exit_timestamp=final_bar.timestamp.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                net_pnl=net_pnl,
            )
        )
        equity_curve[-1]["equity"] = round(cash, 8)

    final_equity = cash
    peak = 0.0
    max_drawdown_pct = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, (peak - equity) / peak * 100.0
            )

    completed_trades = len(trades)
    wins = sum(1 for trade in trades if trade["net_pnl"] > 0)
    first_close = closes[execution_start_index - 1]
    benchmark_return_pct = ((closes[-1] / first_close) - 1.0) * 100.0
    evaluated_bars_count = (
        len(bars)
        if evaluation_start_index == 0
        else len(bars) - evaluation_start_index
    )
    return {
        "bars_count": evaluated_bars_count,
        "initial_cash": round(initial_cash, 8),
        "final_equity": round(final_equity, 8),
        "total_return_pct": round((final_equity / initial_cash - 1.0) * 100.0, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "number_of_trades": completed_trades,
        "win_rate_pct": round((wins / completed_trades) * 100.0, 6)
        if completed_trades
        else 0.0,
        "benchmark_return_pct": round(benchmark_return_pct, 6),
        "equity_curve": equity_curve,
        "trades": trades,
        "assumptions": {
            "execution": "signal at completed close; fill at next bar open",
            "positioning": "long-only, all available cash, no leverage",
            "signal": "close beyond channels built from prior completed bars only",
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "open_position": "closed at final close if still open",
        },
    }


def _trade_record(
    *,
    entry_timestamp: str | None,
    exit_timestamp: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    entry_fee: float,
    exit_fee: float,
    net_pnl: float,
) -> dict[str, Any]:
    return {
        "entry_timestamp": entry_timestamp,
        "exit_timestamp": exit_timestamp,
        "entry_price": round(entry_price, 8),
        "exit_price": round(exit_price, 8),
        "quantity": round(quantity, 8),
        "entry_fee": round(entry_fee, 8),
        "exit_fee": round(exit_fee, 8),
        "net_pnl": round(net_pnl, 8),
    }
