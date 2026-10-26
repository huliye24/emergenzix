"""Probability-theory analysis of the 699 baseline trades.

Answers, from the empirical R distribution alone:
  1. moments of R and the breakeven identity p*W = 1-p
  2. Kelly growth g(f) = E[log(1 + f*R)] and the growth-optimal fraction
  3. how much growth 1% risk actually delivers, and why 10% destroys accounts
  4. Monte Carlo drawdown / ruin distribution at each risk fraction
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from pathlib import Path

SOURCE = Path(r"C:\Users\Administrator\AppData\Local\Temp\xsec_payload.json")
TRADES_PER_YEAR = 18.0
YEARS = 4
SEED = 20261026


def moments(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.fmean(values)
    var = statistics.fmean((x - mean) ** 2 for x in values)
    sd = math.sqrt(var)
    m3 = statistics.fmean((x - mean) ** 3 for x in values)
    m4 = statistics.fmean((x - mean) ** 4 for x in values)
    return {
        "n": n, "mean": round(mean, 4), "sd": round(sd, 4),
        "variance": round(var, 4),
        "skew": round(m3 / sd ** 3, 3) if sd else None,
        "excess_kurtosis": round(m4 / sd ** 4 - 3, 3) if sd else None,
        "min": round(min(values), 3), "max": round(max(values), 3),
        "median": round(statistics.median(values), 4),
        "p01": round(sorted(values)[max(0, int(0.01 * n))], 3),
        "p99": round(sorted(values)[min(n - 1, int(0.99 * n))], 3),
    }


def growth(values: list[float], f: float) -> float | None:
    """Mean log growth per trade at risk fraction f (None if any 1+f*R <= 0)."""
    total = 0.0
    for r in values:
        argument = 1.0 + f * r
        if argument <= 0:
            return None
        total += math.log(argument)
    return total / len(values)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    base = [t for t in payload["trades"]
            if t["cell"] == "R0" and t["max_leverage"] == 3.0 and t["risk_pct"] == 1.0]
    rs = [t["r_multiple"] for t in base if t["r_multiple"] is not None]
    print(f"baseline trades: {len(rs)}")

    out: dict = {"trades": len(rs), "trades_per_year": TRADES_PER_YEAR}

    # ---- 1. moments + breakeven identity ----
    out["moments"] = moments(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    p = len(wins) / len(rs)
    w = statistics.fmean(wins)
    loss_mean = statistics.fmean(losses)
    out["payoff_identity"] = {
        "win_rate_p": round(p, 4),
        "avg_win_R": round(w, 4),
        "avg_loss_R": round(loss_mean, 4),
        "product_p_times_W": round(p * w, 4),
        "loss_term_1_minus_p": round((1 - p) * abs(loss_mean), 4),
        "edge_per_trade_R": round(p * w + (1 - p) * loss_mean, 4),
        "breakeven_win_rate_for_this_W": round(abs(loss_mean) / (w + abs(loss_mean)), 4),
        "note": ("期望 = p*W - (1-p)*L。要让它变大只有三条路：提高 p、提高 W、降低 L。"
                 "L 已被止损固定在 1.03R 左右，所以只剩 p 与 W。"),
    }

    # ---- 2. Kelly growth curve ----
    grid = [i / 1000 for i in range(0, 401, 5)]
    curve = []
    best_f, best_g = 0.0, -1e9
    for f in grid:
        g = growth(rs, f)
        if g is None:
            curve.append({"f_pct": round(f * 100, 2), "g_per_trade": None,
                          "g_annual_pct": None})
            continue
        curve.append({"f_pct": round(f * 100, 2), "g_per_trade": g,
                      "g_annual_pct": (math.exp(g * TRADES_PER_YEAR) - 1) * 100})
        if g > best_g:
            best_g, best_f = g, f
    out["kelly"] = {
        "optimal_f_pct": round(best_f * 100, 2),
        "optimal_g_per_trade": round(best_g, 5),
        "optimal_annual_growth_pct": round((math.exp(best_g * TRADES_PER_YEAR) - 1) * 100, 2),
        "half_kelly_pct": round(best_f * 50, 2),
        "curve": curve,
    }

    # ---- 3. growth at the risk levels actually used ----
    levels = {}
    for f_pct in (1.0, 2.0, 3.0, 5.0, 7.5, 10.0):
        g = growth(rs, f_pct / 100)
        levels[f"{f_pct:g}%"] = {
            "g_per_trade": None if g is None else round(g, 6),
            "annual_growth_pct": None if g is None else round((math.exp(g * TRADES_PER_YEAR) - 1) * 100, 2),
            "ruin_impossible": g is not None,
        }
    out["growth_at_levels"] = levels

    # ---- 4. Monte Carlo drawdowns (iid bootstrap of the empirical R) ----
    random.seed(SEED)
    simulations = 20000
    horizon = int(TRADES_PER_YEAR * YEARS)
    mc = {}
    for f_pct in (1.0, 2.0, 3.0, 5.0, 7.5, 10.0):
        f = f_pct / 100
        finals, max_dds, ruins = [], [], 0
        for _ in range(simulations):
            equity = 1.0
            peak = 1.0
            worst = 0.0
            busted = False
            for _ in range(horizon):
                r = rs[random.randrange(len(rs))]
                equity *= (1 + f * r)
                if equity <= 0:
                    busted = True
                    break
                peak = max(peak, equity)
                worst = max(worst, (peak - equity) / peak)
            finals.append(equity)
            max_dds.append(worst)
            if busted or equity <= 0.5:
                ruins += 1
        finals.sort()
        max_dds.sort()
        mc[f"{f_pct:g}%"] = {
            "median_final_multiple": round(finals[len(finals) // 2], 3),
            "p05_final_multiple": round(finals[int(0.05 * simulations)], 3),
            "p95_final_multiple": round(finals[int(0.95 * simulations)], 3),
            "median_max_dd_pct": round(max_dds[len(max_dds) // 2] * 100, 1),
            "p95_max_dd_pct": round(max_dds[int(0.95 * simulations)] * 100, 1),
            "prob_max_dd_over_50pct": round(
                sum(1 for d in max_dds if d > 0.50) / simulations * 100, 1),
            "prob_equity_halved_or_worse": round(ruins / simulations * 100, 1),
        }
    out["monte_carlo"] = {
        "simulations": simulations, "horizon_trades": horizon,
        "note": "iid 自助重抽样（会低估真实连亏风险，因为忽略了波动率聚集与相关性）",
        "results": mc,
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    Path(r"E:\NEMT Simulator\.artifacts\math\probability_analysis.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
