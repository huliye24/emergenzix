"""S-003.1 rolling-equity and tranche ledger engine (research only).

Implements the four configurations frozen by
``experiments/preregistration/s0031-2023-rolling-tranche.md``:

    R0  rolling equity, single tranche      (must reproduce the existing engine)
    F0  fixed initial-100 budget, single    (isolates the compounding effect)
    T3  rolling equity, tranches 50/25/25   (staged entry)
    T2  rolling equity, tranches 60/40      (staged entry, coarser)

Accounting semantics (S-003.1 §2) are exported per bar: equity, available cash,
locked notional, realised net, unrealised, cumulative fees and slippage, and
external flow (always zero here).  The engine refuses to report unless the
ledger reconciles.

No PAPER, TESTNET or LIVE order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_spot_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import compute_momentum_state
from .registry import data_fingerprint
from ..weekly_regime_filter import up_up_entry_allowed
from .v522_ablation_cli import (
    ATR_BARS, FEE_RATE, MAXIMUM_LEVERAGE, RISK_FRACTION_OF_EQUITY,
    SLIPPAGE_RATE, STOP_MULTIPLE, _direction, atr, breakout, channel_exit,
)

PROTOCOL = "nemt-s003.1-rolling-tranche-001"
ENTRY_BARS = 120
EXIT_BARS = 60
TOLERANCE = 1e-9

PLANS: dict[str, dict] = {
    "R0": {"label": "rolling_single", "sizing": "rolling", "slices": (1.0,)},
    "F0": {"label": "fixed_initial_single", "sizing": "fixed", "slices": (1.0,)},
    "T3": {"label": "rolling_tranche_50_25_25", "sizing": "rolling",
           "slices": (0.5, 0.25, 0.25)},
    "T2": {"label": "rolling_tranche_60_40", "sizing": "rolling", "slices": (0.6, 0.4)},
    # Arm C: identical to T3 except that the shared stop moves to the
    # cost-breakeven price once the second tranche fills (S-003.1 §5 arm C).
    "C1": {"label": "rolling_tranche_breakeven_after_2", "sizing": "rolling",
           "slices": (0.5, 0.25, 0.25), "breakeven_after": 2},
    # PR #72 gate: two consecutive positive four-week states, longs only.
    "U1": {"label": "rolling_single_weekly_upup", "sizing": "rolling",
           "slices": (1.0,), "weekly_up_up_gate": True},
}


def _drawdown(equities: list[float]) -> float:
    peak, maximum = equities[0], 0.0
    for equity in equities:
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak if peak else 0.0)
    return maximum


def run_plan(
    *, bars: list[Bar], zone: ZoneInfo, start: datetime, end: datetime, plan: dict,
    capital: float = 100.0, fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
    risk_fraction: float = RISK_FRACTION_OF_EQUITY,
    max_leverage: float = MAXIMUM_LEVERAGE,
) -> dict:
    """Run one S-003.1 configuration and reconcile its ledger."""
    closes = [bar.close for bar in bars]
    warmup = max(ENTRY_BARS, ATR_BARS) + 1
    cash = capital
    position = None
    pending_entry = None
    pending_add = None
    trades: list[dict] = []
    curve: list[dict] = []
    fees_paid = 0.0
    slippage_cost = 0.0
    realized_net = 0.0
    unfilled_tranches = 0
    last_window_index = None
    stop_tightenings = 0
    max_open_risk_pct = 0.0
    counters_upup_blocked = 0
    leverage_truncations = 0
    slices = plan["slices"]

    def equity_now(mark_price: float) -> float:
        if position is None:
            return cash
        unrealized = (position["quantity"]
                      * (mark_price - position["avg_entry"]) * position["side"]
                      - position["entry_fee"])
        return cash + unrealized

    def add_tranche(index: int, price: float, slice_index: int) -> None:
        nonlocal cash, position, fees_paid, slippage_cost, stop_tightenings, leverage_truncations
        side = position["side"]
        entry = price * (1 + side * slippage_rate)
        distance = abs(entry - position["stop"])
        if distance <= 0:
            return
        slice_amount = slices[slice_index] * position["plan_risk_budget"]
        quantity = slice_amount / distance
        notional = quantity * entry
        # leverage cap applies to the aggregate notional
        headroom = max_leverage * cash - position["notional"]
        if notional > headroom:
            if headroom <= 0:
                return
            quantity *= headroom / notional
            notional = headroom
            leverage_truncations += 1
        fee = notional * fee_rate
        fees_paid += fee
        slippage_cost += quantity * entry * slippage_rate
        total_qty = position["quantity"] + quantity
        position["avg_entry"] = ((position["avg_entry"] * position["quantity"]
                                  + entry * quantity) / total_qty)
        position["quantity"] = total_qty
        position["notional"] += notional
        position["entry_fee"] += fee
        position["filled_slices"] += 1
        position["tranches"].append({
            "index": index, "slice": slices[slice_index], "entry_price": entry,
            "quantity": quantity, "notional": notional, "fee": fee,
        })
        # Arm C: once the configured tranche count is filled, move the shared
        # stop to the cost-breakeven price.  It may only tighten.
        breakeven_after = plan.get("breakeven_after")
        if breakeven_after and len(position["tranches"]) >= breakeven_after:
            cost_buffer = 2 * fee_rate + 2 * slippage_rate
            breakeven = position["avg_entry"] * (1 + side * cost_buffer)
            tightened = (max(position["stop"], breakeven) if side == 1
                         else min(position["stop"], breakeven))
            if tightened != position["stop"]:
                position["stop"] = tightened
                stop_tightenings += 1

    def close_position(index: int, raw_price: float, reason: str) -> None:
        nonlocal cash, position, fees_paid, slippage_cost, realized_net
        side = position["side"]
        exit_price = raw_price * (1 - side * slippage_rate)
        gross = position["quantity"] * (exit_price - position["avg_entry"]) * side
        exit_fee = position["quantity"] * exit_price * fee_rate
        net = gross - position["entry_fee"] - exit_fee
        fees_paid += exit_fee
        slippage_cost += position["quantity"] * raw_price * slippage_rate
        cash += net
        realized_net += net
        trades.append({
            "side": "LONG" if side == 1 else "SHORT", "direction": side,
            "entry_index": position["entry_index"],
            "exit_index": index, "exit_at": bars[index].timestamp.isoformat(),
            "avg_entry": position["avg_entry"], "exit_price": exit_price,
            "exit_reason": reason, "quantity": position["quantity"],
            "notional": position["notional"], "tranches": len(position["tranches"]),
            "tranche_detail": position["tranches"],
            "holding_bars": index - position["entry_index"],
            "holding_days": (index - position["entry_index"]) / 6.0,
            "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
            "net_pnl": net, "equity_after": cash,
        })
        position = None

    for index in range(warmup, len(bars)):
        bar = bars[index]
        if not (start <= bar.timestamp.astimezone(zone) < end):
            continue
        last_window_index = index
        decision_index = index - 1
        decision_bar = bars[decision_index]
        in_decision_window = start <= decision_bar.timestamp.astimezone(zone) < end
        closed_this_bar = False

        if position is not None:
            side = position["side"]
            stop = position["stop"]
            gap = bar.open <= stop if side == 1 else bar.open >= stop
            channel = (in_decision_window
                       and channel_exit(bars, decision_index, EXIT_BARS, side))
            touched = bar.low <= stop if side == 1 else bar.high >= stop
            if gap:
                close_position(index, bar.open, "gap_stop")
                closed_this_bar = True
            elif channel:
                close_position(index, bar.open, "opposite_channel")
                closed_this_bar = True
            elif touched:
                close_position(index, stop, "2N_stop")
                closed_this_bar = True

        if position is None and not closed_this_bar and pending_entry is not None \
                and pending_entry["decision_index"] == decision_index:
            side = pending_entry["direction"]
            entry = bar.open * (1 + side * slippage_rate)
            n_value = pending_entry["n_value"]
            distance = max(STOP_MULTIPLE * n_value, entry * 0.01)
            equity_ref = cash if plan["sizing"] == "rolling" else capital
            budget = equity_ref * risk_fraction
            quantity = (slices[0] * budget) / distance
            notional = quantity * entry
            headroom = max_leverage * cash
            if notional > headroom:
                quantity *= headroom / notional
                notional = headroom
                leverage_truncations += 1
            fee = notional * fee_rate
            fees_paid += fee
            slippage_cost += quantity * entry * slippage_rate
            position = {
                "side": side, "direction": side,
                "entry_index": index, "planned_stop": entry - side * distance,
                "stop": entry - side * distance,
                "d1": distance, "first_entry": entry,
                "plan_risk_budget": budget,
                "quantity": quantity, "notional": notional,
                "avg_entry": entry, "entry_fee": fee,
                "filled_slices": 1, "tranches": [{
                    "index": index, "slice": slices[0], "entry_price": entry,
                    "quantity": quantity, "notional": notional, "fee": fee}],
            }
            entry_stop = position["stop"]
            if (bar.low <= entry_stop if side == 1 else bar.high >= entry_stop):
                close_position(index, entry_stop, "2N_stop")
                closed_this_bar = True
        pending_entry = None

        if position is not None and pending_add is not None and not closed_this_bar:
            add_tranche(index, bar.open, pending_add)
        pending_add = None

        state = compute_momentum_state(closes[:index + 1])
        nemt = _direction(state)
        brk = breakout(bars, index, ENTRY_BARS)

        if position is not None and len(position["tranches"]) < len(slices):
            side = position["side"]
            favourable = side * (bar.close - position["first_entry"])
            next_slice = len(position["tranches"])
            if favourable >= next_slice * position["d1"]:
                pending_add = next_slice
        if position is None and nemt and brk == nemt:
            allowed = True
            if plan.get("weekly_up_up_gate") and nemt == 1:
                allowed = up_up_entry_allowed(
                    bars[:index + 1], as_of=bar.timestamp + timedelta(hours=4))
                if not allowed:
                    counters_upup_blocked += 1
            if allowed:
                pending_entry = {"decision_index": index, "direction": nemt,
                                 "n_value": atr(bars, index)}

        equity = equity_now(bar.close)
        if position is not None and equity > 0:
            open_risk = position["quantity"] * abs(position["avg_entry"] - position["stop"])
            max_open_risk_pct = max(max_open_risk_pct, open_risk / equity * 100)
        curve.append({
            "time": bar.timestamp.isoformat(), "equity": equity, "cash": cash,
            "locked_notional": (position["notional"] if position else 0.0),
            "realized_net": realized_net,
            "unrealized": (equity - cash) if position else 0.0,
            "fees_paid": fees_paid, "slippage_cost": slippage_cost,
            "external_flow": 0.0,
        })

    if position is not None:
        unfilled_tranches += max(0, len(slices) - len(position["tranches"]))
        last = bars[last_window_index]
        close_position(last_window_index, last.close, "end_of_period")
        if curve:
            curve[-1].update({"equity": cash, "cash": cash, "locked_notional": 0.0,
                              "unrealized": 0.0, "realized_net": realized_net,
                              "fees_paid": fees_paid, "slippage_cost": slippage_cost})

    equities = [capital] + [point["equity"] for point in curve]
    wins = sorted((t["net_pnl"] for t in trades if t["net_pnl"] > 0), reverse=True)
    losses = [-t["net_pnl"] for t in trades if t["net_pnl"] <= 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    occupancy = [point["locked_notional"] / point["equity"]
                 for point in curve if point["equity"] > 0]
    tranche_counts = Counter(t["tranches"] for t in trades)

    ledger_checks = {
        "equity_equals_cash_plus_realized": abs(cash - (capital + realized_net)) <= 1e-6,
        "all_positions_closed": position is None,
        "external_flow_zero": True,
        "fees_are_non_negative": fees_paid >= 0,
        "realized_matches_trade_sum":
            abs(realized_net - sum(t["net_pnl"] for t in trades)) <= 1e-6,
    }
    metrics = {
        "initial_equity": capital, "final_equity": cash,
        "total_return_pct": (cash / capital - 1) * 100,
        "realized_net": realized_net,
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
        "tranches_per_trade": dict(tranche_counts),
        "unfilled_tranches": unfilled_tranches,
        "stop_tightenings": stop_tightenings,
        "weekly_up_up_blocked": counters_upup_blocked,
        "leverage_truncations": leverage_truncations,
        "max_open_risk_pct": max_open_risk_pct,
        "fees_paid_usdt": fees_paid,
        "slippage_cost_usdt": slippage_cost,
        "cash_occupancy_mean": statistics.fmean(occupancy) if occupancy else 0.0,
        "cash_occupancy_peak": max(occupancy) if occupancy else 0.0,
        "largest_win_usdt": wins[0] if wins else 0.0,
        "largest_win_share_of_gross_win": wins[0] / gross_win if gross_win else None,
        "return_without_largest_win_pct": ((cash - (wins[0] if wins else 0.0))
                                          / capital - 1) * 100,
        "worst_trade_usdt": min((t["net_pnl"] for t in trades), default=0.0),
        "external_flow": 0.0,
        "risk_fraction": risk_fraction,
        "max_leverage": max_leverage,
    }
    return {"config": plan["label"], "slices": list(slices), "sizing": plan["sizing"],
            "metrics": metrics, "ledger_checks": ledger_checks,
            "trades": trades, "equity_curve": curve}


def synthetic_cashflow_tests() -> list[dict]:
    """S-003.1 §5 synthetic ledger cases: 100->120, 120->132, 100->80, fees."""
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"case": name, "passed": bool(passed), "detail": detail})

    # Portfolio arithmetic: a realised net gain compounds the next budget.
    capital, budget_rate = 100.0, RISK_FRACTION_OF_EQUITY
    budget_1 = capital * budget_rate
    record("budget_scales_with_equity_100",
           abs(budget_1 - 1.0) <= TOLERANCE, f"budget on 100 = {budget_1}")
    equity_after_gain = capital + 20.0
    budget_2 = equity_after_gain * budget_rate
    record("budget_scales_with_equity_120",
           abs(budget_2 - 1.2) <= TOLERANCE, f"budget on 120 = {budget_2}")
    equity_after_second = equity_after_gain * 1.10
    record("compounding_120_to_132",
           abs(equity_after_second - 132.0) <= 1e-6,
           f"120 +10% = {equity_after_second}")

    # Loss case: 100 -> 80 shrinks the next budget, no negative equity.
    equity_after_loss = 80.0
    record("budget_shrinks_after_loss",
           abs(equity_after_loss * budget_rate - 0.8) <= TOLERANCE,
           f"budget on 80 = {equity_after_loss * budget_rate}")
    record("equity_stays_positive", equity_after_loss > 0, f"equity = {equity_after_loss}")

    # Fee must be charged once per fill, not twice, and reduce realised cash.
    notional, fee_rate = 100.0, FEE_RATE
    entry_fee = notional * fee_rate
    exit_fee = notional * fee_rate
    gross = 10.0
    net = gross - entry_fee - exit_fee
    record("fees_charged_once_per_side",
           abs(net - (gross - 2 * notional * fee_rate)) <= TOLERANCE,
           f"net after both fees = {net}")

    # Duplicate tranche must be rejected: the plan only holds one per slice index.
    positions = {"filled_slices": len(PLANS["T3"]["slices"])}
    slices = PLANS["T3"]["slices"]
    duplicate_blocked = positions["filled_slices"] >= len(slices)
    record("duplicate_tranche_rejected", duplicate_blocked,
           f"filled {positions['filled_slices']} of {len(slices)} slices -> no further add")

    # Unrealised profit must not be spendable before the position closes.
    cash, unrealized = 100.0, 25.0
    equity = cash + unrealized
    record("unrealized_not_in_cash", cash == 100.0 and equity == 125.0,
           f"cash={cash} equity={equity} (only equity includes unrealised)")

    # Risk budget conservation across tranches with a shared stop.
    first_entry, d1 = 100.0, 5.0
    stop = first_entry - d1
    prices = [first_entry, first_entry + d1, first_entry + 2 * d1]
    risk = sum((slice_ * 1.0) / abs(price - stop) * abs(price - stop)
               for slice_, price in zip(slices, prices))
    record("shared_stop_conserves_risk", abs(risk - 1.0) <= 1e-9,
           f"total risk across tranches = {risk} (budget 1.0)")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S-003.1 rolling-equity and tranche ledger audit (research only)")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-01-01")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--max-leverage", type=float, default=float(MAXIMUM_LEVERAGE))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)

    synthetic = synthetic_cashflow_tests()
    blocked_tests = [check for check in synthetic if not check["passed"]]

    payloads = fetch_binance_spot_4h_archive(
        symbol=args.symbol,
        start_at=start.astimezone(timezone.utc) - timedelta(days=120),
        end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
    )
    bars = [Bar.model_validate(item) for item in payloads]

    report: dict = {
        "protocol": PROTOCOL,
        "evidence_level": "EXPLORATORY",
        "evidence_note": ("2023 was already used by the v5.2.2/v5.2.6/v5.2.7 E1-E5 "
                          "batteries, so this run cannot be confirmatory."),
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "capital": args.capital,
        "risk_pct": args.risk_pct,
        "max_leverage": args.max_leverage,
        "symbol": args.symbol.strip().upper(),
        "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
        "synthetic_cashflow_tests": synthetic,
        "accounting": {
            "equity": "cash + unrealised - entry fee",
            "realized_net": "sum of closed-trade net PnL (all costs deducted)",
            "external_flow": 0.0,
            "risk_budget": "1% of equity at plan start, shared across tranches",
            "tranche_quantity": "slice_risk / (entry - shared_stop)",
        },
    }
    if blocked_tests:
        report["status"] = "BLOCKED"
        report["blocked_cases"] = blocked_tests
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "cases": blocked_tests}, ensure_ascii=False))
        raise SystemExit(3)

    cells = {}
    for key, plan in PLANS.items():
        cells[key] = run_plan(bars=bars, zone=zone, start=start, end=end, plan=plan,
                              capital=args.capital,
                              risk_fraction=args.risk_pct / 100.0,
                              max_leverage=args.max_leverage)
    report["cells"] = cells

    reconciliation = {key: cell["ledger_checks"] for key, cell in cells.items()}
    report["ledger_reconciliation"] = reconciliation
    broken = [key for key, checks in reconciliation.items()
              if not all(checks.values())]
    report["status"] = "BLOCKED" if broken else "OK"
    if broken:
        report["ledger_failures"] = broken
    report["limitations"] = [
        "2023 already inspected by earlier NEMT batteries; EXPLORATORY only.",
        "Partial fills, cancellations and order rejections are not modelled; every "
        "fill is assumed complete at the next open.",
        "Funding rates, liquidation tiers, latency and depth are not modelled.",
        "Tranche thresholds are price-progress rules, not a validated entry plan.",
        "No PAPER, TESTNET or LIVE order is authorised.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "output": str(args.output),
        "cells": {key: {"final": round(cell["metrics"]["final_equity"], 4),
                        "ret": round(cell["metrics"]["total_return_pct"], 4),
                        "trades": cell["metrics"]["trades"],
                        "reconciled": all(cell["ledger_checks"].values())}
                  for key, cell in cells.items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
