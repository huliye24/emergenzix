"""NEMT v5.2.7: does price-occupancy entropy add value beyond momentum psi?

Frozen by ``experiments/preregistration/v527-entropy-incremental-to-psi.md``.
The public Donchian opportunity set is identical to v5.2.6; the only difference
is which gate labels a candidate:

    M_t = 1  iff  breakout direction matches ``|psi| > 0.5``   (psi-only gate)
    F_t = 1  iff  ``S > 0.7``                                  (entropy gate)

    A (psi-only)  accepts iff M_t = 1
    B (psi + S)   accepts iff M_t = 1 and F_t = 1

    Delta_S_raw = mean(R_net | M=1, F=1) - mean(R_net | M=1, F=0)

Research only: no PAPER, TESTNET or LIVE order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import MomentumState, compute_momentum_state
from .registry import data_fingerprint
from .v522_ablation_cli import (
    ARMS, FEE_RATE, SLIPPAGE_RATE, _psi_direction, atr, breakout, run_arm,
)
from .v526_incremental_information_cli import (
    DEDUP_BARS, ENTRY_BARS, WARMUP_BARS, dedupe_events, virtual_trade,
)

PROTOCOL = "nemt-v5.2.7-entropy-incremental-to-psi-001"
ENTROPY_THRESHOLD = 0.7
PSI_THRESHOLD = 0.5
MIN_EVENTS_PER_GROUP = 10


def build_psi_ledger(
    *, bars: list[Bar], zone: ZoneInfo, start: datetime, end: datetime,
    state_fn=compute_momentum_state, fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> list[dict]:
    """Enumerate every breakout and label it with the psi and entropy gates."""
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
        psi_side = _psi_direction(state)
        trade = virtual_trade(bars=bars, decision_index=index, side=side, end=end,
                              fee_rate=fee_rate, slippage_rate=slippage_rate)
        events.append({
            "at": bar.timestamp.isoformat(),
            "month": bar.timestamp.astimezone(zone).strftime("%Y-%m"),
            "decision_index": index,
            "breakout_direction": side,
            "psi": state.psi, "entropy": state.entropy,
            "m": 1 if psi_side == side else 0,
            "f": 1 if state.entropy > ENTROPY_THRESHOLD else 0,
            "psi_over_price": state.psi,
            "atr_over_price": (
                atr(bars, index) / bar.close if bar.close else 0.0
            ),
            **trade,
        })
    return events


def _summarize(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "min": min(values),
            "max": max(values)}


def delta_s(events: list[dict], *, bootstrap_rounds: int = 2000,
            seed: int = 20261027) -> dict:
    """Estimate Delta_S_raw with a month-block bootstrap confidence interval."""
    candidates = [e for e in events if e["m"] == 1]
    closed = [e for e in candidates if e["status"] == "CLOSED" and e["r_net"] is not None]
    high = [e for e in closed if e["f"] == 1]
    low = [e for e in closed if e["f"] == 0]

    result: dict = {
        "m_events": len(candidates),
        "m_closed": len(closed),
        "m_censored": sum(e["status"] == "CENSORED" for e in candidates),
        "f1_events": len(high), "f0_events": len(low),
        "f1_mean_r": statistics.mean([e["r_net"] for e in high]) if high else None,
        "f0_mean_r": statistics.mean([e["r_net"] for e in low]) if low else None,
        "psi_magnitude": {
            "f1": _summarize([abs(e["psi"]) for e in high]),
            "f0": _summarize([abs(e["psi"]) for e in low]),
        },
        "atr_over_price": {
            "f1": _summarize([e["atr_over_price"] for e in high]),
            "f0": _summarize([e["atr_over_price"] for e in low]),
        },
        "direction_split": {
            "f1_long": sum(e["breakout_direction"] == 1 for e in high),
            "f1_short": sum(e["breakout_direction"] == -1 for e in high),
            "f0_long": sum(e["breakout_direction"] == 1 for e in low),
            "f0_short": sum(e["breakout_direction"] == -1 for e in low),
        },
        "delta_s_raw": None, "ci95": None, "status": "INCONCLUSIVE",
    }

    rejected = sum(1 for e in candidates if e["f"] == 0)
    if rejected == 0 or rejected == len(candidates):
        result["status"] = "NO_FILTER_VARIATION"
        result["reason"] = "entropy_gate_rejects_nothing_or_everything"
        return result
    if len(high) < MIN_EVENTS_PER_GROUP or len(low) < MIN_EVENTS_PER_GROUP:
        result["reason"] = "insufficient_closed_events_in_a_group"
        return result

    point = result["f1_mean_r"] - result["f0_mean_r"]
    result["delta_s_raw"] = point

    by_month: dict[str, list[dict]] = {}
    for event in closed:
        by_month.setdefault(event["month"], []).append(event)
    months = sorted(by_month)
    overlapping = [
        month for month in months
        if {e["f"] for e in by_month[month]} == {0, 1}
    ]
    result["months_total"] = len(months)
    result["months_with_both_groups"] = len(overlapping)
    if len(months) < 3:
        result["reason"] = "insufficient_time_blocks"
        return result

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(bootstrap_rounds):
        pool: list[dict] = []
        for _ in months:
            pool.extend(by_month[rng.choice(months)])
        a = [e["r_net"] for e in pool if e["f"] == 1]
        b = [e["r_net"] for e in pool if e["f"] == 0]
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
    """Math and classification checks required by protocol 搂5."""
    zone = ZoneInfo("Asia/Shanghai")
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    window = (t0, t0 + timedelta(days=400))
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"case": name, "passed": bool(passed), "detail": detail})

    # 1) Entropy is invariant to the time ordering of the same price multiset,
    #    while psi is not.  This is the protocol 搂2 claim.
    grid = [100.0 + 10.0 * step for step in range(10)] * 12
    ascending = compute_momentum_state(grid)
    descending = compute_momentum_state(list(reversed(grid)))
    record("entropy_permutation_invariant",
           abs(ascending.entropy - descending.entropy) < 1e-12
           and abs(ascending.psi - descending.psi) > 1.0,
           f"S={ascending.entropy:.6f} vs {descending.entropy:.6f}; "
           f"psi={ascending.psi:+.4f} vs {descending.psi:+.4f}")

    def series(closes: list[float]) -> list[Bar]:
        out, previous = [], closes[0]
        for index, close in enumerate(closes):
            open_price = previous if index else close
            out.append(Bar(timestamp=t0 + timedelta(hours=4) * index, open=open_price,
                           high=max(open_price, close), low=min(open_price, close),
                           close=close, volume=1.0))
            previous = close
        return out

    bars = series([100.0] * 340 + [120.0, 120.0])

    def ledger_for(psi: float, entropy: float):
        def state_fn(closes, config=None):
            return MomentumState(psi, entropy, "stable", 90)

        return build_psi_ledger(bars=bars, zone=zone, start=window[0],
                                end=window[1], state_fn=state_fn)

    high = ledger_for(1.0, 0.9)
    low = ledger_for(1.0, 0.2)
    opposite = ledger_for(-1.0, 0.9)
    weak = ledger_for(0.4, 0.9)
    boundary = ledger_for(1.0, ENTROPY_THRESHOLD)

    record("psi_only_gate_plus_entropy",
           len(high) == 1 and high[0]["m"] == 1 and high[0]["f"] == 1,
           f"m={high[0]['m'] if high else None} f={high[0]['f'] if high else None}")
    record("psi_only_gate_without_entropy",
           len(low) == 1 and low[0]["m"] == 1 and low[0]["f"] == 0,
           f"m={low[0]['m'] if low else None} f={low[0]['f'] if low else None}")
    record("opposite_psi_is_not_a_candidate",
           len(opposite) == 1 and opposite[0]["m"] == 0,
           f"m={opposite[0]['m'] if opposite else None}")
    record("weak_psi_is_not_a_candidate",
           len(weak) == 1 and weak[0]["m"] == 0,
           f"psi=0.4 m={weak[0]['m'] if weak else None}")
    record("entropy_boundary_is_exclusive",
           len(boundary) == 1 and boundary[0]["f"] == 0,
           f"S=0.7 f={boundary[0]['f'] if boundary else None}")

    # 6/7) execution invariants inherited from v5.2.3 / v5.2.4.
    spike = series([100.0] * 340 + [120.0, 120.0])
    entry_index = len(spike) - 1  # the breakout at 340 fills on this bar
    spike[entry_index] = Bar(timestamp=spike[entry_index].timestamp,
                             open=spike[entry_index].open, high=spike[entry_index].open,
                             low=1.0, close=spike[entry_index].open, volume=1.0)
    same_bar = build_psi_ledger(bars=spike, zone=zone, start=window[0], end=window[1],
                                state_fn=lambda c, config=None: MomentumState(1.0, 0.9, "stable", 90))
    record("entry_bar_stop_hits",
           bool(same_bar) and same_bar[0].get("exit_reason") == "2N_stop"
           and same_bar[0].get("holding_bars") == 0,
           f"reason={same_bar[0].get('exit_reason') if same_bar else None}")

    gapped = series([100.0] * 340 + [120.0, 120.0, 120.0])
    gap_open = 60.0
    gap_index = len(gapped) - 1
    gapped[gap_index] = Bar(timestamp=gapped[gap_index].timestamp,
                                  open=gap_open, high=gap_open, low=gap_open,
                                  close=gap_open, volume=1.0)
    gap = build_psi_ledger(bars=gapped, zone=zone, start=window[0], end=window[1],
                           state_fn=lambda c, config=None: MomentumState(1.0, 0.9, "stable", 90))
    record("gap_stop_fills_at_open",
           bool(gap) and gap[0].get("exit_reason") == "gap_stop"
           and gap[0].get("exit_price") is not None
           and abs(gap[0]["exit_price"] - gap_open * (1 - SLIPPAGE_RATE)) < 1e-9,
           f"reason={gap[0].get('exit_reason') if gap else None}")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NEMT v5.2.7 entropy-versus-psi test (research only)")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-01-01")
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
    ledger = build_psi_ledger(bars=bars, zone=zone, start=start, end=end)
    m_candidates = [e for e in ledger if e["m"] == 1]
    deduped = dedupe_events(m_candidates, gap=DEDUP_BARS)

    report: dict = {
        "protocol": PROTOCOL,
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
        "frozen_definitions": {
            "opportunity_set": "all 120-bar Donchian breakouts of completed bar t",
            "m_gate": f"breakout direction equals sign(psi) and |psi| > {PSI_THRESHOLD}",
            "f_gate": f"price-occupancy entropy S > {ENTROPY_THRESHOLD} (boundary exclusive)",
            "exit_bars": 60, "atr_bars": 120, "stop": "max(2N, 1% of entry)",
            "costs": {"fee_bps_each_side": FEE_RATE * 10_000,
                      "slippage_bps_each_side": SLIPPAGE_RATE * 10_000},
            "dedupe": f"first same-direction event per {DEDUP_BARS} completed bars",
        },
        "synthetic_validation": checks,
        "status": "BLOCKED" if blocked else "OK",
    }
    if blocked:
        report["blocked_cases"] = blocked
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(json.dumps({"status": "BLOCKED"}, ensure_ascii=False))
        raise SystemExit(3)

    psi_only = run_arm(bars=bars, arm=ARMS["A"], zone=zone, start=start, end=end,
                       capital=args.capital, gate="psi")
    psi_entropy = run_arm(bars=bars, arm=ARMS["A"], zone=zone, start=start, end=end,
                          capital=args.capital, gate="nemt")

    def summarize(arm_report: dict, gate: str) -> dict:
        return {"arm": arm_report["arm_label"], "gate": gate,
                "metrics": arm_report["metrics"], "counters": arm_report["counters"],
                "exit_reasons": dict(Counter(t["exit_reason"] for t in arm_report["trades"]))}

    report["layer1_event_level"] = {
        "all_breakouts": len(ledger),
        "m_candidates": len(m_candidates),
        "m_candidates_deduped": len(deduped),
        "by_status": dict(Counter(e["status"] for e in ledger)),
        "delta_s_raw": delta_s(ledger, bootstrap_rounds=args.bootstrap_rounds),
        "delta_s_raw_deduped": delta_s(deduped, bootstrap_rounds=args.bootstrap_rounds),
        "events": ledger,
    }
    report["layer2_portfolio"] = {
        "a_psi_only": summarize(psi_only, "psi"),
        "b_psi_plus_entropy": summarize(psi_entropy, "nemt"),
        "note": "Portfolio PnL differences also reflect path-dependent opportunity "
                "sets and exposure time, not only the Delta_S_raw selection effect.",
    }
    report["limitations"] = [
        "2025 and 2023 are both already exposed to prior v5 research decisions; treat "
        "any window that was inspected before freezing as EXPLORATORY.",
        "Price-occupancy entropy is not thermodynamic entropy and does not by itself "
        "demonstrate a non-equilibrium phase transition.",
        "Groups may differ in |psi| and volatility; Delta_S_raw is not a controlled "
        "matched comparison unless a frozen stratification is applied.",
        "Single asset; funding, liquidation tiers, latency and depth are not modeled.",
        "No PAPER, TESTNET or LIVE order was created.",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "output": str(args.output),
        "all_breakouts": len(ledger), "m_candidates": len(m_candidates),
        "delta_s_raw": report["layer1_event_level"]["delta_s_raw"]["delta_s_raw"],
        "layer2": {k: v["metrics"]["total_return_pct"]
                   for k, v in report["layer2_portfolio"].items()
                   if isinstance(v, dict) and "metrics" in v},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
