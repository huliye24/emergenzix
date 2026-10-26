"""NEMT v5.2.6: incremental information of the critical-state gate.

This module answers exactly one question, as frozen by
``experiments/preregistration/v526-nemt-incremental-information.md``:

    Relative to the same Donchian breakout rule, does the NEMT
    ``critical_up/critical_down`` permission add cost-adjusted conditional
    predictive information?

Two independent layers are produced from one shared candidate set:

1. **All-event ledger** -- every 120-bar breakout, including the ones NEMT
   rejects, receives its own virtual single trade and a net risk-unit result
   ``R_net``.  ``Delta_R = mean(R_net | G=1) - mean(R_net | G=0)``.
2. **Two independent single-account ledgers** -- Donchian-only versus
   Donchian+NEMT, sharing the v5.2.3/v5.2.4 execution semantics.

Nothing in the frozen NEMT definition, the 120/60 channel, the ATR estimator,
the 2N stop or the cost assumptions is varied here.  Research only: no PAPER,
TESTNET or LIVE order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import compute_momentum_state
from .registry import data_fingerprint
from .v522_ablation_cli import (
    ARMS, FEE_RATE, SLIPPAGE_RATE, _direction, atr, breakout, channel_exit, run_arm,
)

PROTOCOL = "nemt-v5.2.6-nemt-incremental-information-001"
WARMUP_BARS = 330
ENTRY_BARS = 120
EXIT_BARS = 60
DEDUP_BARS = 12
REFERENCE_CAPITAL = 100.0
STOP_MULTIPLE = 2.0
MIN_EVENTS_PER_GROUP = 10


def _side_label(side: int) -> str:
    return "LONG" if side == 1 else "SHORT"


def _close_out(entry: float, exit_price: float, side: int, quantity: float,
               risk: float, fee_rate: float) -> float:
    gross = quantity * (exit_price - entry) * side
    fees = quantity * entry * fee_rate + quantity * exit_price * fee_rate
    return (gross - fees) / risk


def virtual_trade(
    *, bars: list[Bar], decision_index: int, side: int, end: datetime,
    fee_rate: float = FEE_RATE, slippage_rate: float = SLIPPAGE_RATE,
) -> dict:
    """Simulate one independent virtual trade for a single breakout event."""
    entry_index = decision_index + 1
    if entry_index >= len(bars):
        return {"status": "UNAVAILABLE", "reason": "no_entry_bar"}
    entry = bars[entry_index].open * (1 + side * slippage_rate)
    n_value = atr(bars, decision_index)
    distance = max(STOP_MULTIPLE * n_value, entry * 0.01)
    stop = entry - side * distance
    quantity = REFERENCE_CAPITAL / entry
    risk = quantity * distance
    base = {
        "decision_index": decision_index, "entry_index": entry_index,
        "side": _side_label(side), "direction": side, "entry_price": entry,
        "n_value": n_value, "stop_distance": distance, "stop": stop,
        "planned_risk_usdt": risk,
    }

    def done(index: int, price: float, reason: str) -> dict:
        exit_price = price * (1 - side * slippage_rate)
        return {**base, "status": "CLOSED", "exit_index": index,
                "exit_reason": reason, "exit_price": exit_price,
                "holding_bars": index - entry_index,
                "holding_days": (index - entry_index) / 6.0,
                "r_net": _close_out(entry, exit_price, side, quantity, risk, fee_rate)}

    entry_bar = bars[entry_index]
    if (entry_bar.low <= stop if side == 1 else entry_bar.high >= stop):
        return done(entry_index, stop, "2N_stop")

    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        if bar.timestamp >= end:
            mark = bars[index - 1].close
            return {**base, "status": "CENSORED", "exit_index": None,
                    "exit_reason": "window_end", "r_net": None,
                    "r_net_mark_to_market": _close_out(
                        entry, mark * (1 - side * slippage_rate), side, quantity, risk,
                        fee_rate)}
        gap = bar.open <= stop if side == 1 else bar.open >= stop
        if gap:
            return done(index, bar.open, "gap_stop")
        if channel_exit(bars, index - 1, EXIT_BARS, side):
            return done(index, bar.open, "opposite_channel")
        if (bar.low <= stop if side == 1 else bar.high >= stop):
            return done(index, stop, "2N_stop")
    return {**base, "status": "UNAVAILABLE", "reason": "no_bars_after_entry", "r_net": None}


def build_ledger(
    *, bars: list[Bar], zone: ZoneInfo, start: datetime, end: datetime,
    state_fn=compute_momentum_state, fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> list[dict]:
    """Enumerate every 120-bar breakout and attach its counterfactual result."""
    if len(bars) <= WARMUP_BARS + 1:
        return []
    closes = [bar.close for bar in bars]
    events: list[dict] = []
    for index in range(WARMUP_BARS, len(bars)):
        bar = bars[index]
        if not (start <= bar.timestamp.astimezone(zone) < end):
            continue
        side = breakout(bars, index, ENTRY_BARS)
        if side == 0:
            continue
        state = state_fn(closes[:index + 1])
        nemt = _direction(state)
        trade = virtual_trade(bars=bars, decision_index=index, side=side, end=end,
                              fee_rate=fee_rate, slippage_rate=slippage_rate)
        events.append({
            "at": bar.timestamp.isoformat(),
            "month": bar.timestamp.astimezone(zone).strftime("%Y-%m"),
            "decision_index": index,
            "breakout_direction": side,
            "nemt_direction": nemt,
            "g": 1 if nemt == side else 0,
            "psi": state.psi, "entropy": state.entropy, "phase": state.phase,
            **trade,
        })
    return events


def dedupe_events(events: list[dict], gap: int = DEDUP_BARS) -> list[dict]:
    """Keep the first event per direction inside each ``gap``-bar window."""
    kept: list[dict] = []
    last = {1: -10_000, -1: -10_000}
    for event in sorted(events, key=lambda item: item["decision_index"]):
        side = event["breakout_direction"]
        if event["decision_index"] - last[side] >= gap:
            kept.append(event)
            last[side] = event["decision_index"]
    return kept


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def delta_r(events: list[dict], *, bootstrap_rounds: int = 2000,
            seed: int = 20261026) -> dict:
    """Estimate Delta_R with a month-block bootstrap confidence interval."""
    complete = [e for e in events if e["status"] == "CLOSED" and e["r_net"] is not None]
    allowed = [e for e in complete if e["g"] == 1]
    refused = [e for e in complete if e["g"] == 0]
    result = {
        "closed_events": len(complete),
        "censored_events": sum(e["status"] == "CENSORED" for e in events),
        "unavailable_events": sum(e["status"] == "UNAVAILABLE" for e in events),
        "g1_events": len(allowed), "g0_events": len(refused),
        "g1_mean_r": _mean([e["r_net"] for e in allowed]),
        "g0_mean_r": _mean([e["r_net"] for e in refused]),
        "delta_r": None, "ci95": None, "status": "INCONCLUSIVE",
    }
    if len(allowed) < MIN_EVENTS_PER_GROUP or len(refused) < MIN_EVENTS_PER_GROUP:
        result["reason"] = "insufficient_events_in_a_group"
        return result

    point = result["g1_mean_r"] - result["g0_mean_r"]
    result["delta_r"] = point

    by_month: dict[str, list[dict]] = {}
    for event in complete:
        by_month.setdefault(event["month"], []).append(event)
    months = sorted(by_month)
    if len(months) < 3:
        result["reason"] = "insufficient_time_blocks"
        result["status"] = "INCONCLUSIVE"
        return result

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(bootstrap_rounds):
        pool: list[dict] = []
        for _ in months:
            pool.extend(by_month[rng.choice(months)])
        a = [e["r_net"] for e in pool if e["g"] == 1]
        b = [e["r_net"] for e in pool if e["g"] == 0]
        if a and b:
            draws.append(statistics.mean(a) - statistics.mean(b))
    if len(draws) < bootstrap_rounds // 2:
        result["reason"] = "degenerate_bootstrap_resamples"
        return result
    draws.sort()
    result["ci95"] = [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]]
    result["status"] = "ESTIMATED"
    result["ci_excludes_zero_positive"] = result["ci95"][0] > 0
    return result


def synthetic_validation() -> list[dict]:
    """Protocol 搂5.4: verify event classification on synthetic 4h candles."""
    zone = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)

    def series(closes: list[float]) -> list[Bar]:
        out, previous = [], closes[0]
        for index, close in enumerate(closes):
            open_price = previous if index else close
            out.append(Bar(timestamp=t0 + timedelta(hours=4) * index, open=open_price,
                           high=max(open_price, close), low=min(open_price, close),
                           close=close, volume=1.0))
            previous = close
        return out

    def state_with(phase: str, psi: float):
        from .nemt_momentum_v2 import MomentumState

        return lambda closes, config=None: MomentumState(psi, 0.9, phase, 90)

    up = state_with("critical_up", 1.0)
    down = state_with("critical_down", -1.0)
    flat = state_with("stable", 0.0)
    window = (t0, t0 + timedelta(days=400))
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"case": name, "passed": bool(passed), "detail": detail})

    # 1/2: breakout honoured or refused by NEMT.
    base = [100.0] * 340 + [120.0, 120.0]
    bars = series(base)
    permitted = build_ledger(bars=bars, zone=zone, start=window[0], end=window[1], state_fn=up)
    refused = build_ledger(bars=bars, zone=zone, start=window[0], end=window[1], state_fn=flat)
    record("breakout_and_permitted_g1",
           len(permitted) == 1 and permitted[0]["g"] == 1, f"events={len(permitted)}")
    record("breakout_but_not_permitted_g0",
           len(refused) == 1 and refused[0]["g"] == 0, f"events={len(refused)}")

    # 3: NEMT critical without any breakout must not create an event.
    quiet = series([100.0 + (index % 2) * 0.01 for index in range(400)])
    no_break = build_ledger(bars=quiet, zone=zone, start=window[0], end=window[1], state_fn=up)
    record("no_breakout_no_event", len(no_break) == 0, f"events={len(no_break)}")

    # 4: opposite NEMT direction is a refusal.
    opposite = build_ledger(bars=bars, zone=zone, start=window[0], end=window[1], state_fn=down)
    record("opposite_critical_g0",
           len(opposite) == 1 and opposite[0]["g"] == 0, f"events={len(opposite)}")

    # 5: too little history cannot produce candidates.
    short = build_ledger(bars=series([100.0] * 100), zone=zone, start=window[0],
                         end=window[1], state_fn=up)
    record("insufficient_window_unavailable", len(short) == 0, f"events={len(short)}")

    # 6: protective stop inside the entry bar.
    spike = series([100.0] * 340 + [120.0, 120.0])
    entry_index = 341
    spike[entry_index] = Bar(timestamp=spike[entry_index].timestamp,
                             open=spike[entry_index].open, high=spike[entry_index].open,
                             low=1.0, close=spike[entry_index].open, volume=1.0)
    same_bar = build_ledger(bars=spike, zone=zone, start=window[0], end=window[1], state_fn=up)
    record("entry_bar_stop_hits",
           len(same_bar) == 1 and same_bar[0].get("exit_reason") == "2N_stop"
           and same_bar[0].get("holding_bars") == 0,
           f"reason={same_bar[0].get('exit_reason') if same_bar else None}")

    # 7: a gapped open fills at the open, never at the stop price.
    gapped = series([100.0] * 340 + [120.0, 120.0, 120.0])
    gap_open = 60.0
    gapped[342] = Bar(timestamp=gapped[342].timestamp, open=gap_open,
                      high=gap_open, low=gap_open, close=gap_open, volume=1.0)
    gap = build_ledger(bars=gapped, zone=zone, start=window[0], end=window[1], state_fn=up)
    ok = bool(gap) and gap[0].get("exit_reason") == "gap_stop" \
        and gap[0].get("exit_price") is not None \
        and abs(gap[0]["exit_price"] - gap_open * (1 - SLIPPAGE_RATE)) < 1e-9
    record("gap_stop_fills_at_open", ok,
           f"reason={gap[0].get('exit_reason') if gap else None}")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NEMT v5.2.6 incremental-information test (research only)")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-01-01")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--bootstrap-rounds", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)

    checks = synthetic_validation()
    blocked = [c for c in checks if not c["passed"]]

    payloads = fetch_binance_btc_4h_archive(
        start_at=start.astimezone(timezone.utc) - timedelta(days=WARMUP_BARS * 4 // 24 + 40),
        end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
    )
    bars = [Bar.model_validate(item) for item in payloads]
    ledger = build_ledger(bars=bars, zone=zone, start=start, end=end)
    deduped = dedupe_events(ledger)

    report: dict = {
        "protocol": PROTOCOL,
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
        "frozen_definitions": {
            "donchian": "close of t versus highs/lows of the 120 completed bars before t",
            "nemt": "psi>0.5 and entropy>0.7 (critical_up) / psi<-0.5 (critical_down)",
            "gate": "G=1 iff breakout direction equals the NEMT critical direction",
            "exit_bars": EXIT_BARS, "atr_bars": 120, "stop": "max(2N, 1% of entry)",
            "risk": "1% of equity notional", "leverage_cap": 3.0,
            "dedupe": f"first same-direction event per {DEDUP_BARS} completed bars",
            "costs": {"fee_bps_each_side": FEE_RATE * 10_000,
                      "slippage_bps_each_side": SLIPPAGE_RATE * 10_000},
        },
        "synthetic_validation": checks,
        "status": "BLOCKED" if blocked else "OK",
    }
    if blocked:
        report["blocked_cases"] = blocked
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(json.dumps({"status": "BLOCKED", "output": str(args.output)}, ensure_ascii=False))
        raise SystemExit(3)

    allowance = run_arm(bars=bars, arm=ARMS["A"], zone=zone, start=start, end=end,
                        capital=args.capital, gate="nemt")
    baseline = run_arm(bars=bars, arm=ARMS["A"], zone=zone, start=start, end=end,
                       capital=args.capital, gate="none")

    def summarize(arm_report: dict) -> dict:
        return {"arm": arm_report["arm_label"], "gate": arm_report.get("gate", "nemt"),
                "metrics": arm_report["metrics"], "counters": arm_report["counters"],
                "exit_reasons": dict(Counter(t["exit_reason"] for t in arm_report["trades"]))}

    report["layer1_all_event_ledger"] = {
        "all_events": len(ledger),
        "deduped_events": len(deduped),
        "by_status": dict(Counter(e["status"] for e in ledger)),
        "delta_r": delta_r(ledger, bootstrap_rounds=args.bootstrap_rounds),
        "delta_r_deduped": delta_r(deduped, bootstrap_rounds=args.bootstrap_rounds),
        "direction_split": {
            "long_events": sum(e["breakout_direction"] == 1 for e in ledger),
            "short_events": sum(e["breakout_direction"] == -1 for e in ledger),
            "long_g1": sum(e["breakout_direction"] == 1 and e["g"] == 1 for e in ledger),
            "short_g1": sum(e["breakout_direction"] == -1 and e["g"] == 1 for e in ledger),
        },
        "events": ledger,
    }
    report["layer2_portfolio_ledgers"] = {
        "num_trades": 0, "profit_factor": None, "payoff_ratio": None,
    }
    report["layer2_portfolio_ledgers"] = {
        "baseline_donchian_only": summarize(baseline),
        "with_nemt_permission": summarize(allowance),
        "note": "Two independent single-account ledgers. PnL differences also reflect "
                "path-dependent opportunity sets, not only the Delta_R selection effect.",
    }
    report["limitations"] = [
        "2025 has been inspected by v5-alpha, v5.1 and v5.2; this run is EXPLORATORY, "
        "not a blind out-of-sample replication.",
        "Observational association only: momentum, volatility, trend maturity and timing "
        "can drive both the NEMT decision and future outcomes.",
        "Single asset (BTCUSDT spot); no cross-market claim.",
        "Funding, liquidation tiers, latency and order-book depth are not modeled.",
        "No PAPER, TESTNET or LIVE order was created.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "output": str(args.output),
        "all_events": len(ledger), "deduped": len(deduped),
        "delta_r": report["layer1_all_event_ledger"]["delta_r"],
        "baseline": baseline["metrics"], "allowance": allowance["metrics"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
