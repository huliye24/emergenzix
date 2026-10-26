"""True portfolio simulation: one account, all symbols, real concurrency limit.

This is the number that answers "收益" under real constraints:
  * one shared equity curve
  * each new trade risks f of CURRENT equity
  * at most `max_concurrent` positions open (leverage cap 3x / stop ~2.5% -> ~7 max)
  * trades processed in chronological order of entry
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

OUT = pathlib.Path("/home/ubuntu/nemt-lo/out")
TARGET = pathlib.Path("/home/ubuntu/nemt-lo/portfolio.json")
ARMS = ["R0", "F2", "P1", "L1", "L2", "L3"]
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load_events(arm: str) -> list[dict]:
    events = []
    for path in sorted(OUT.glob("*.json")):
        d = json.load(open(path, encoding="utf-8"))
        cell = d["cells"][arm]
        for t in cell["trades"]:
            before = t["equity_after"] - t["net_pnl"]
            risk = 0.01 * before
            if risk <= 0:
                continue
            r = t["net_pnl"] / risk
            exit_at = datetime.fromisoformat(t["exit_at"].replace("Z", "+00:00"))
            entry_at = exit_at - timedelta(hours=4 * t["holding_bars"])
            events.append({"entry": entry_at, "exit": exit_at, "r": r})
    events.sort(key=lambda e: e["entry"])
    return events


def simulate(events: list[dict], f: float, max_concurrent: int,
             aggregate_risk_cap: float | None = None) -> dict:
    equity = 1.0
    open_positions = []   # list of (exit_time, pnl_fraction)
    peak = 1.0
    worst_dd = 0.0
    taken = 0
    skipped = 0
    for event in events:
        # settle everything that closed before this entry
        still_open = []
        for exit_time, pnl_fraction, risked in open_positions:
            if exit_time <= event["entry"]:
                equity += pnl_fraction
            else:
                still_open.append((exit_time, pnl_fraction, risked))
        open_positions = still_open
        peak = max(peak, equity)
        worst_dd = max(worst_dd, (peak - equity) / peak)
        if equity <= 0:
            equity = 0.0
            break
        if len(open_positions) >= max_concurrent:
            skipped += 1
            continue
        if aggregate_risk_cap is not None:
            open_risk = sum(risked for _, _, risked in open_positions)
            if (open_risk + equity * f) / equity > aggregate_risk_cap:
                skipped += 1
                continue
        risked = equity * f
        open_positions.append((event["exit"], risked * event["r"], risked))
        taken += 1
    for exit_time, pnl_fraction, risked in sorted(open_positions):
        equity += pnl_fraction
    years = (END - START).days / 365.25
    annual = (equity ** (1 / years) - 1) * 100 if equity > 0 else -100.0
    return {"final_multiple": round(equity, 4),
            "annual_return_pct": round(annual, 2),
            "max_drawdown_pct": round(worst_dd * 100, 1),
            "trades_taken": taken, "trades_skipped": skipped}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    grid_f = [0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10]
    # 3x notional cap with a ~2.5% stop implies aggregate risk <= ~7.5%
    caps = [3, 5, 7]
    out = {"years": 4.0, "grid": {}, "best": {}}
    for arm in ARMS:
        events = load_events(arm)
        out["grid"][arm] = {}
        best = None
        rows = []
        for cap in caps:
            for f in grid_f:
                result = simulate(events, f, cap, aggregate_risk_cap=0.075)
                rows.append((cap, f, result))
                key = f"cap{cap}_f{f*100:g}"
                out["grid"][arm][key] = result
                if result["final_multiple"] > 0 and (
                        best is None or result["annual_return_pct"] > best[2]["annual_return_pct"]):
                    best = (cap, f, result)
        out["best"][arm] = {"max_concurrent": best[0], "risk_pct": round(best[1] * 100, 2),
                            **best[2], "trades_available": len(events)}
    print(f"{'arm':4s} {'trades':>7s} {'best cap':>9s} {'risk':>6s} {'final x':>10s} "
          f"{'annual':>9s} {'maxDD':>8s} {'taken':>6s} {'skipped':>8s}")
    for arm in ARMS:
        b = out["best"][arm]
        print(f"{arm:4s} {b['trades_available']:7d} {b['max_concurrent']:9d} "
              f"{b['risk_pct']:5.2f}% {b['final_multiple']:10.2f} "
              f"{b['annual_return_pct']:+8.1f}% {b['max_drawdown_pct']:7.1f}% "
              f"{b['trades_taken']:6d} {b['trades_skipped']:8d}")

    print()
    print("=== L1 detail (aggregate risk capped at 7.5% = 3x notional, ~2.5% stops) ===")
    print(f"{'cap':>4s}" + "".join(f"{('f'+str(f*100)+'%'):>11s}" for f in grid_f))
    for cap in caps:
        line = f"{cap:4d}"
        for f in grid_f:
            v = out["grid"]["L1"][f"cap{cap}_f{f*100:g}"]
            line += f"{v['annual_return_pct']:+10.1f}%"
        print(line)

    TARGET.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {TARGET}")


if __name__ == "__main__":
    main()
