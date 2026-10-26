"""Evaluate the low-frequency / pyramiding arms by Kelly ceiling, and test the
hedge proposition mathematically (variance reduction vs mean reduction)."""

from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict

OUT = pathlib.Path("/home/ubuntu/nemt-lowfreq/out")
TARGET = pathlib.Path("/home/ubuntu/nemt-lowfreq/analysis.json")
ARMS = ["R0", "F1", "F2", "P1"]
TRADES_PER_YEAR = 18.0


def kelly(rs: list[float]) -> dict:
    best_f, best_g = 0.0, -1e9
    f = 0.0
    while f <= 0.30:
        total, ok = 0.0, True
        for r in rs:
            argument = 1.0 + f * r
            if argument <= 0:
                ok = False
                break
            total += math.log(argument)
        if ok:
            g = total / len(rs)
            if g > best_g:
                best_g, best_f = g, f
        f += 0.0025
    annual = (math.exp(best_g * TRADES_PER_YEAR) - 1) * 100 if best_g > -1e9 else None
    return {"optimal_f_pct": round(best_f * 100, 2),
            "g_per_trade": round(best_g, 6) if best_g > -1e9 else None,
            "annual_pct": round(annual, 2) if annual is not None else None}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    files = sorted(OUT.glob("*.json"))
    per_arm = defaultdict(list)
    per_arm_year = defaultdict(dict)
    counts = defaultdict(int)
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        sym, yr = f.stem.split("_")
        for arm in ARMS:
            c = d["cells"][arm]
            for t in c["trades"]:
                before = t["equity_after"] - t["net_pnl"]
                risk_usdt = 0.01 * before
                if risk_usdt > 0:
                    per_arm[arm].append(t["net_pnl"] / risk_usdt)
            per_arm_year[arm][f"{sym}_{yr}"] = c["metrics"]["total_return_pct"]
            counts[arm] += c["metrics"]["trades"]
    print(f"files={len(files)}")
    for arm in ARMS:
        print(f"  {arm}: trades={len(per_arm[arm])}")

    result = {"files": len(files), "arms": {}}
    print()
    print(f"{'arm':4s} {'trades':>7s} {'/yr':>6s} {'meanR':>8s} {'sdR':>8s} {'win%':>6s} "
          f"{'f*':>6s} {'g/trade':>9s} {'annual':>8s} {'pos/40':>7s}")
    for arm in ARMS:
        rs = per_arm[arm]
        k = kelly(rs)
        wins = [r for r in rs if r > 0]
        years = per_arm_year[arm]
        positive = sum(1 for v in years.values() if v > 0)
        per_year = len(rs) / 4.0
        result["arms"][arm] = {
            "trades": len(rs), "trades_per_year": round(per_year, 2),
            "mean_R": round(statistics.fmean(rs), 4),
            "sd_R": round(statistics.pstdev(rs), 4),
            "win_rate_pct": round(len(wins) / len(rs) * 100, 2),
            "kelly": k,
            "positive_asset_years": positive,
            "asset_years": len(years),
            "median_return_pct": round(statistics.median(years.values()), 3),
            "mean_return_pct": round(statistics.fmean(years.values()), 3),
        }
        print(f"{arm:4s} {len(rs):7d} {per_year:6.1f} {statistics.fmean(rs):+8.3f} "
              f"{statistics.pstdev(rs):8.3f} {len(wins)/len(rs)*100:6.1f} "
              f"{k['optimal_f_pct']:6.2f} {k['g_per_trade']:9.5f} "
              f"{k['annual_pct']:+7.2f}% {positive:3d}/{len(years):<3d}")

    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("baseline R0 annual ceiling from the previous (risk-sweep) analysis: +14.64%")


if __name__ == "__main__":
    main()
