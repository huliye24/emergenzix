"""NEMT v5.2: symmetric Turtle-style weekly trend backtest."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import compute_momentum_state


def atr(bars: list[Bar], index: int, period: int = 120) -> float:
    values = []
    for cursor in range(index - period + 1, index + 1):
        previous = bars[cursor - 1].close
        values.append(max(bars[cursor].high - bars[cursor].low,
                          abs(bars[cursor].high - previous),
                          abs(bars[cursor].low - previous)))
    return statistics.mean(values)


def breakout(bars: list[Bar], index: int, lookback: int) -> int:
    history = bars[index - lookback:index]
    return 1 if bars[index].close > max(bar.high for bar in history) else (
        -1 if bars[index].close < min(bar.low for bar in history) else 0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-01-01")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)
    bars = [Bar.model_validate(item) for item in fetch_binance_btc_4h_archive(
        start_at=start.astimezone(timezone.utc) - timedelta(days=500),
        end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
    )]
    closes = [bar.close for bar in bars]
    cash, position = args.capital, None
    trades, curve, audit = [], [], []
    fee, slippage = 0.0005, 0.0002
    last_index = 331

    for index in range(331, len(bars)):
        bar = bars[index]
        local_open = bar.timestamp.astimezone(zone)
        if local_open < start or local_open >= end:
            continue
        last_index = index
        decision_index = index - 1
        state = compute_momentum_state(closes[:decision_index + 1])

        if position:
            side = position["direction"]
            stop_hit = bar.low <= position["stop"] if side == 1 else bar.high >= position["stop"]
            exit_window = position["exit_window"]
            history = bars[decision_index - exit_window:decision_index]
            channel_exit = (bars[decision_index].close < min(item.low for item in history)
                            if side == 1 else
                            bars[decision_index].close > max(item.high for item in history))
            if stop_hit or channel_exit:
                raw_exit = position["stop"] if stop_hit else bar.open
                exit_price = raw_exit * (1 - side * slippage)
                gross = position["quantity"] * (exit_price - position["entry_price"]) * side
                exit_fee = position["quantity"] * exit_price * fee
                net = gross - position["entry_fee"] - exit_fee
                cash += net
                trades.append({**position, "exit_at": bar.timestamp.isoformat(),
                               "exit_price": exit_price,
                               "exit_reason": "2N_stop" if stop_hit else "opposite_channel",
                               "holding_bars": index - position["entry_index"],
                               "holding_days": (index - position["entry_index"]) / 6,
                               "net_pnl": net, "equity_after": cash})
                position = None

        if position is None:
            direction = 1 if state.phase == "critical_up" else -1 if state.phase == "critical_down" else 0
            fast = breakout(bars, decision_index, 120)
            slow = breakout(bars, decision_index, 330)
            system = "system_1_20d" if fast == direction and direction else (
                "system_2_55d" if slow == direction and direction else None
            )
            if direction:
                audit.append({"at": bars[decision_index].timestamp.isoformat(),
                              "nemt_direction": direction, "fast_breakout": fast,
                              "slow_breakout": slow, "accepted": system is not None})
            if system:
                entry = bar.open * (1 + direction * slippage)
                n_value = atr(bars, decision_index)
                stop_distance = max(2.0 * n_value, entry * 0.01)
                risk_fraction = stop_distance / entry
                notional = min(cash * 3.0, cash * 0.01 / risk_fraction)
                quantity = notional / entry
                position = {
                    "system": system, "direction": direction,
                    "side": "LONG" if direction == 1 else "SHORT",
                    "entry_index": index, "entry_at": bar.timestamp.isoformat(),
                    "entry_price": entry, "quantity": quantity, "notional": notional,
                    "leverage": notional / cash, "entry_fee": notional * fee,
                    "n_value": n_value, "stop": entry - direction * stop_distance,
                    "exit_window": 60 if system == "system_1_20d" else 120,
                    "initial_risk_usdt": cash * 0.01,
                    "nemt_psi": state.psi, "nemt_entropy": state.entropy,
                }

        equity = cash
        if position:
            equity += position["quantity"] * (bar.close - position["entry_price"]) * position["direction"]
            equity -= position["entry_fee"]
        curve.append({"time": bar.timestamp.isoformat(), "equity": equity})

    if position:
        last = next(bar for bar in reversed(bars) if bar.timestamp.astimezone(zone) < end)
        side = position["direction"]
        exit_price = last.close * (1 - side * slippage)
        gross = position["quantity"] * (exit_price - position["entry_price"]) * side
        exit_fee = position["quantity"] * exit_price * fee
        net = gross - position["entry_fee"] - exit_fee
        cash += net
        trades.append({**position, "exit_at": (last.timestamp + timedelta(hours=4)).isoformat(),
                       "exit_price": exit_price, "exit_reason": "end_of_period",
                       "holding_bars": last_index - position["entry_index"] + 1,
                       "holding_days": (last_index - position["entry_index"] + 1) / 6,
                       "net_pnl": net, "equity_after": cash})
        curve[-1]["equity"] = cash

    equities = [args.capital] + [item["equity"] for item in curve]
    peak, max_dd = equities[0], 0.0
    for equity in equities:
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
    wins = [trade for trade in trades if trade["net_pnl"] > 0]
    losses = [trade for trade in trades if trade["net_pnl"] <= 0]
    gross_win = sum(trade["net_pnl"] for trade in wins)
    gross_loss = abs(sum(trade["net_pnl"] for trade in losses))
    metrics = {
        "initial_equity": args.capital, "final_equity": cash,
        "total_return_pct": (cash / args.capital - 1) * 100,
        "max_drawdown_pct": max_dd * 100, "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "payoff_ratio": ((gross_win / len(wins)) / (gross_loss / len(losses))) if wins and losses else None,
        "average_holding_days": statistics.mean(t["holding_days"] for t in trades) if trades else 0.0,
        "longs": sum(t["side"] == "LONG" for t in trades),
        "shorts": sum(t["side"] == "SHORT" for t in trades),
        "nemt_directional_events": len(audit),
        "accepted_breakouts": sum(item["accepted"] for item in audit),
    }
    report = {
        "protocol": "nemt-v5.2-turtle-dual-breakout-001",
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "rules": {"direction": "NEMT critical phase", "system_1": "120/60 bars",
                  "system_2": "330/120 bars", "atr_n_bars": 120,
                  "stop": "2N", "risk_per_trade": 0.01, "maximum_leverage": 3,
                  "pyramiding": False, "fixed_take_profit": False},
        "metrics": metrics, "trades": trades, "candidate_audit": audit,
        "equity_curve": curve,
        "limitations": ["Single BTC market; original Turtles diversified across futures.",
                        "Funding, liquidation tiers, latency and depth are not modeled.",
                        "Historical simulation only; no orders were created."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": metrics}, ensure_ascii=False))


if __name__ == "__main__":
    main()
