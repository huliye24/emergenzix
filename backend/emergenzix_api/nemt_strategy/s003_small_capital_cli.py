"""S-003.W small-capital weekly trend capture: single-variable audit engine.

Implements the four frozen cells of
``experiments/preregistration/s003w-2022-exploratory.md``:

    B0  baseline = existing arm A (120-bar entry / 60-bar exit + NEMT gate)
    W1  + weekly direction confirmation on entry
    W2  exit channel 60 -> 120 bars
    W3  + weekly trend-failure exit
    W4  W1 + W2 + W3

Weekly context is built from completed 4h bars only. A week runs from Monday
00:00 UTC to the next Monday 00:00 UTC (Binance 1W convention); a week becomes
usable for a decision only after it has ended. The weekly trend is the sign of
the last completed weekly close minus the 8-week simple mean of completed
weekly closes.

Research only: no PAPER, TESTNET or LIVE order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import compute_momentum_state
from .registry import data_fingerprint
from .v522_ablation_cli import (
    ATR_BARS, FEE_RATE, MAXIMUM_LEVERAGE, RISK_FRACTION_OF_EQUITY,
    SLIPPAGE_RATE, STOP_MULTIPLE, _direction, atr, breakout, channel_exit,
)

PROTOCOL = "nemt-s003w-small-capital-weekly-001"
ENTRY_BARS = 120
WEEK = timedelta(days=7)
WEEKLY_SMA_WEEKS = 8
OBSERVATION_BARS = 42

CONFIGS: dict[str, dict] = {
    "B0": {"label": "baseline_120_60_nemt", "weekly_gate": False,
           "exit_bars": 60, "weekly_fail_exit": False},
    "W1": {"label": "w1_weekly_confirmation", "weekly_gate": True,
           "exit_bars": 60, "weekly_fail_exit": False},
    "W2": {"label": "w2_long_hold_exit_120", "weekly_gate": False,
           "exit_bars": 120, "weekly_fail_exit": False},
    "W3": {"label": "w3_weekly_failure_exit", "weekly_gate": False,
           "exit_bars": 60, "weekly_fail_exit": True},
    "W4": {"label": "w4_combined", "weekly_gate": True,
           "exit_bars": 120, "weekly_fail_exit": True},
}


def _week_start(timestamp: datetime) -> datetime:
    """Midnight UTC of the Monday that starts this timestamp's week."""
    utc = timestamp.astimezone(timezone.utc)
    midnight = datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc)
    return midnight - timedelta(days=midnight.weekday())


def weekly_context(bars: list[Bar]) -> dict:
    """Completed-week boundaries, weekly closes and their 8-week mean.

    ``ends[k]`` is the moment week ``k`` finished, so week ``k`` may only be
    used by a decision taken at or after ``ends[k]``.
    """
    weeks: dict[datetime, list[Bar]] = {}
    order: list[datetime] = []
    for bar in bars:
        start = _week_start(bar.timestamp)
        if start not in weeks:
            weeks[start] = []
            order.append(start)
        weeks[start].append(bar)
    ends = [start + WEEK for start in order]
    closes = [weeks[start][-1].close for start in order]
    trends: list[int | None] = []
    means: list[float | None] = []
    for index in range(len(closes)):
        if index < WEEKLY_SMA_WEEKS - 1:
            trends.append(None)
            means.append(None)
            continue
        window = closes[index - WEEKLY_SMA_WEEKS + 1:index + 1]
        mean = statistics.fmean(window)
        means.append(mean)
        trends.append(1 if closes[index] > mean else -1 if closes[index] < mean else 0)
    return {"ends": ends, "closes": closes, "trends": trends, "means": means}


def trend_at(context: dict, decision_time: datetime) -> int | None:
    """Weekly trend visible at ``decision_time`` (None when history is short)."""
    index = bisect_right(context["ends"], decision_time) - 1
    if index < 0:
        return None
    return context["trends"][index]


def _drawdown(equities: list[float]) -> float:
    peak, maximum = equities[0], 0.0
    for equity in equities:
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak if peak else 0.0)
    return maximum


def run_config(
    *, bars: list[Bar], context: dict, zone: ZoneInfo, start: datetime, end: datetime,
    config: dict, capital: float = 100.0, fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> dict:
    """Run one S-003.W cell over one window."""
    closes = [bar.close for bar in bars]
    warmup = max(ENTRY_BARS, ATR_BARS) + 1
    cash, position, pending = capital, None, None
    trades: list[dict] = []
    curve: list[dict] = []
    counters = {
        "donchian_breakouts": 0,
        "nemt_matching_breakouts": 0,
        "weekly_confirmed": 0,
        "actual_entries": 0,
        "skipped_while_in_position": 0,
        "weekly_fail_exits": 0,
        "entry_bar_stops": 0,
    }
    last_window_index = None

    def close_position(index: int, raw_price: float, reason: str) -> None:
        nonlocal cash, position
        side = position["direction"]
        exit_price = raw_price * (1 - side * slippage_rate)
        gross = position["quantity"] * (exit_price - position["entry_price"]) * side
        exit_fee = position["quantity"] * exit_price * fee_rate
        net = gross - position["entry_fee"] - exit_fee
        cash += net
        trades.append({
            **position, "exit_index": index, "exit_at": bars[index].timestamp.isoformat(),
            "exit_price": exit_price, "exit_reason": reason,
            "holding_bars": index - position["entry_index"],
            "holding_days": (index - position["entry_index"]) / 6.0,
            "net_pnl": net, "equity_after": cash,
        })
        if reason == "weekly_trend_failure":
            counters["weekly_fail_exits"] += 1
        position = None

    for index in range(warmup, len(bars)):
        bar = bars[index]
        if not (start <= bar.timestamp.astimezone(zone) < end):
            continue
        last_window_index = index
        decision_index = index - 1
        decision_bar = bars[decision_index]
        decision_time = decision_bar.timestamp + timedelta(hours=4)
        in_decision_window = start <= decision_bar.timestamp.astimezone(zone) < end
        closed_this_bar = False

        if position is not None:
            side = position["direction"]
            stop = position["stop"]
            gap = bar.open <= stop if side == 1 else bar.open >= stop
            weekly_fail = False
            if config["weekly_fail_exit"] and in_decision_window:
                trend = trend_at(context, decision_time)
                weekly_fail = trend is not None and trend == -side
            channel = (in_decision_window and channel_exit(
                bars, decision_index, position["exit_bars"], side))
            touched = bar.low <= stop if side == 1 else bar.high >= stop
            if gap:
                close_position(index, bar.open, "gap_stop")
                closed_this_bar = True
            elif weekly_fail:
                close_position(index, bar.open, "weekly_trend_failure")
                closed_this_bar = True
            elif channel:
                close_position(index, bar.open, "opposite_channel")
                closed_this_bar = True
            elif touched:
                close_position(index, stop, "2N_stop")
                closed_this_bar = True

        if (position is None and not closed_this_bar and pending is not None
                and pending["decision_index"] == decision_index):
            side = pending["direction"]
            entry = bar.open * (1 + side * slippage_rate)
            n_value = pending["n_value"]
            distance = max(STOP_MULTIPLE * n_value, entry * 0.01)
            risk_fraction = distance / entry
            notional = min(cash * MAXIMUM_LEVERAGE,
                           cash * RISK_FRACTION_OF_EQUITY / risk_fraction)
            quantity = notional / entry
            position = {
                "direction": side, "side": "LONG" if side == 1 else "SHORT",
                "entry_index": index, "entry_at": bar.timestamp.isoformat(),
                "entry_price": entry, "quantity": quantity, "notional": notional,
                "leverage": notional / cash, "entry_fee": notional * fee_rate,
                "n_value": n_value, "stop": entry - side * distance,
                "exit_bars": config["exit_bars"],
                "initial_risk_usdt": cash * RISK_FRACTION_OF_EQUITY,
                "weekly_trend_at_entry": pending["weekly_trend"],
            }
            counters["actual_entries"] += 1
            entry_stop = position["stop"]
            if (bar.low <= entry_stop if side == 1 else bar.high >= entry_stop):
                close_position(index, entry_stop, "2N_stop")
                counters["entry_bar_stops"] += 1
                closed_this_bar = True
        pending = None

        state = compute_momentum_state(closes[:index + 1])
        nemt = _direction(state)
        brk = breakout(bars, index, ENTRY_BARS)
        if brk:
            counters["donchian_breakouts"] += 1
        if nemt and brk == nemt:
            counters["nemt_matching_breakouts"] += 1
            trend = trend_at(context, bar.timestamp + timedelta(hours=4))
            allowed = True
            if config["weekly_gate"]:
                allowed = trend is not None and trend == nemt
                if allowed:
                    counters["weekly_confirmed"] += 1
            if allowed:
                if position is None:
                    pending = {"decision_index": index, "direction": nemt,
                               "n_value": atr(bars, index), "weekly_trend": trend}
                else:
                    counters["skipped_while_in_position"] += 1

        unrealized = 0.0
        if position is not None:
            unrealized = (position["quantity"]
                          * (bar.close - position["entry_price"]) * position["direction"]
                          - position["entry_fee"])
        curve.append({"time": bar.timestamp.isoformat(), "equity": cash + unrealized})

    if position is not None:
        last = bars[last_window_index]
        close_position(last_window_index, last.close, "end_of_period")
        if curve:
            curve[-1]["equity"] = cash

    wins = sorted((t["net_pnl"] for t in trades if t["net_pnl"] > 0), reverse=True)
    losses = [-t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    equities = [capital] + [point["equity"] for point in curve]
    observable = [t for t in trades if t["exit_reason"] != "end_of_period"]
    early = [t for t in observable
             if t["holding_bars"] <= OBSERVATION_BARS
             and t["exit_reason"] in {"2N_stop", "gap_stop"}]
    metrics = {
        "initial_equity": capital, "final_equity": cash,
        "total_return_pct": (cash / capital - 1) * 100,
        "max_drawdown_pct": _drawdown(equities) * 100 if equities else 0.0,
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "payoff_ratio": ((gross_win / len(wins)) / (gross_loss / len(losses))
                         if wins and losses else None),
        "average_holding_days": (statistics.mean(t["holding_days"] for t in trades)
                                 if trades else 0.0),
        "longs": sum(t["side"] == "LONG" for t in trades),
        "shorts": sum(t["side"] == "SHORT" for t in trades),
        "early_protective_stop_ratio": len(early) / len(observable) if observable else None,
        "largest_win_usdt": wins[0] if wins else 0.0,
        "largest_win_share_of_gross_win": wins[0] / gross_win if gross_win else None,
        "return_without_largest_win_pct": ((cash - (wins[0] if wins else 0.0))
                                          / capital - 1) * 100,
        "return_without_top2_wins_pct": ((cash - sum(wins[:2])) / capital - 1) * 100,
    }
    return {"config": config["label"], "metrics": metrics, "counters": counters,
            "trades": trades, "equity_curve": curve}


def weekly_timing_validation() -> list[dict]:
    """Prove that an unfinished week never influences a decision."""
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)  # a Monday
    bars: list[Bar] = []
    price = 100.0
    for step in range(12 * 7 * 6):
        bars.append(Bar(timestamp=t0 + timedelta(hours=4) * step, open=price,
                        high=price, low=price, close=price, volume=1.0))
    # A violent rally confined to the current, still-unfinished week.
    for extra in range(6):
        stamp = t0 + timedelta(days=84) + timedelta(hours=4) * extra
        value = 100.0 + 20.0 * (extra + 1)
        bars.append(Bar(timestamp=stamp, open=100.0, high=value, low=100.0,
                        close=value, volume=1.0))
    context = weekly_context(bars)
    mid_week = t0 + timedelta(days=84, hours=24)  # Wednesday of the open week
    trend_mid = trend_at(context, mid_week)
    trend_next = trend_at(context, t0 + timedelta(days=91))
    checks = [
        {"case": "unfinished_week_ignored",
         "passed": trend_mid == 0,
         "detail": f"trend during the open rally week = {trend_mid} (expected 0)"},
        {"case": "week_visible_after_it_ends",
         "passed": trend_next == 1,
         "detail": f"trend after the week completed = {trend_next} (expected +1)"},
        {"case": "weekly_sma_requires_eight_weeks",
         "passed": trend_at(context, t0 + timedelta(days=20)) is None,
         "detail": "fewer than eight completed weeks must return None"},
    ]
    return checks


def cost_grid(*, bars, context, zone, start, end, config, capital) -> list[dict]:
    grid = []
    for fee_bps in (5.0, 10.0, 15.0):
        for slippage_bps in (2.0, 5.0, 10.0):
            report = run_config(
                bars=bars, context=context, zone=zone, start=start, end=end,
                config=config, capital=capital, fee_rate=fee_bps / 10_000.0,
                slippage_rate=slippage_bps / 10_000.0,
            )
            grid.append({
                "fee_bps_per_side": fee_bps, "slippage_bps_per_side": slippage_bps,
                "final_equity": report["metrics"]["final_equity"],
                "total_return_pct": report["metrics"]["total_return_pct"],
                "trades": report["metrics"]["trades"],
            })
    return grid


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S-003.W small-capital weekly trend audit (research only)")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2023-01-01")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)

    checks = weekly_timing_validation()
    blocked = [check for check in checks if not check["passed"]]

    payloads = fetch_binance_btc_4h_archive(
        start_at=start.astimezone(timezone.utc) - timedelta(days=120),
        end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
    )
    bars = [Bar.model_validate(item) for item in payloads]
    context = weekly_context(bars)

    report: dict = {
        "protocol": PROTOCOL,
        "evidence_level": "EXPLORATORY",
        "evidence_note": (
            "2022 was already inspected by the v5.2.7 E1-E5 battery in this session, "
            "so this run cannot be a confirmatory observation for S-003.W."),
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
        "weekly_context": {
            "week_boundary": "Monday 00:00 UTC",
            "trend_rule": "sign(last completed weekly close - SMA8(completed weekly closes))",
            "completed_weeks": len(context["ends"]),
            "usable_weekly_trends": sum(1 for value in context["trends"] if value is not None),
        },
        "synthetic_checks": checks,
        "status": "BLOCKED" if blocked else "OK",
    }
    if blocked:
        report["blocked_cases"] = blocked
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "output": str(args.output)}, ensure_ascii=False))
        raise SystemExit(3)

    cells = {}
    for key, config in CONFIGS.items():
        result = run_config(bars=bars, context=context, zone=zone, start=start, end=end,
                            config=config, capital=args.capital)
        if args.stress:
            result["cost_grid"] = cost_grid(bars=bars, context=context, zone=zone,
                                            start=start, end=end, config=config,
                                            capital=args.capital)
        cells[key] = result
    report["cells"] = cells
    report["interpretation_rules"] = {
        "single_variable": "W1/W2/W3 each change exactly one element versus B0",
        "combined_not_attributable": "W4 varies three elements at once; no single-factor claim",
        "small_sample": "any cell with fewer than 10 trades caps the conclusion at INCONCLUSIVE",
        "no_order_permission": "no PAPER, TESTNET or LIVE order is authorised",
    }
    report["limitations"] = [
        "2022 already inspected by the v5.2.7 battery; EXPLORATORY only.",
        "Single asset, no funding, no liquidation tiers, no latency or depth model.",
        "Removing the largest winner is a diagnostic stress test, not a trading rule.",
        "S-003 explicitly forbids treating this run as confirmation of the track.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "output": str(args.output),
        "cells": {key: {"final": round(cell["metrics"]["final_equity"], 4),
                        "ret": round(cell["metrics"]["total_return_pct"], 4),
                        "trades": cell["metrics"]["trades"]}
                  for key, cell in cells.items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
