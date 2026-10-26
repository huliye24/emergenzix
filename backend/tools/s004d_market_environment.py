"""S-004.D — market environment and direction asymmetry (research only).

Links the frozen BTC-based environment buckets to the SAME 2,356 candidates used
in A.2 / A.3.2, then reports:

  * net-R distribution per environment cell, split by direction
  * long-minus-short asymmetry with a WEEKLY-CLUSTER block bootstrap
  * year-by-year sign stability
  * portfolio layer: is the profit one market episode (week concentration and
    cross-symbol correlation of weekly R)?

Environment variables use only completed BTC bars at or before the decision cutoff.
No thresholds are tuned here; they are fixed in the frozen protocol.
"""

from __future__ import annotations

import csv
import json
import math
import pathlib
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, "/home/ubuntu/nemt-d/backend")

from emergenzix_api.historical_data import fetch_binance_spot_4h_archive  # noqa: E402
from emergenzix_api.nemt_strategy.s004a3_state import causal_direction_states  # noqa: E402
from emergenzix_api.nemt_strategy.v522_ablation_cli import (  # noqa: E402
    ATR_BARS, FEE_RATE, SLIPPAGE_RATE, STOP_MULTIPLE, atr, breakout, channel_exit,
)
from emergenzix_api.schemas import Bar  # noqa: E402

ENTRY_BARS, EXIT_BARS, WARMUP_DAYS = 120, 60, 420
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
YEARS = [2022, 2023, 2024, 2025]
OUT = pathlib.Path("/home/ubuntu/nemt-d")
SEED, BOOTSTRAP = 20261026, 2000
STEP = timedelta(hours=4)

TREND_BARS, TREND_LONG_BARS, VOL_BARS, VOL_YEAR_BARS = 180, 540, 180, 1620


def simulate_candidate(bars: list[Bar], entry_index: int, side: int, n_value: float) -> dict:
    entry = bars[entry_index].open * (1 + side * SLIPPAGE_RATE)
    distance = max(STOP_MULTIPLE * n_value, entry * 0.01)
    stop = entry - side * distance
    exit_price = reason = None
    exit_index = entry_index
    if (bars[entry_index].low <= stop) if side == 1 else (bars[entry_index].high >= stop):
        exit_price, reason = stop, "entry_bar_stop"
    else:
        for index in range(entry_index, len(bars)):
            bar = bars[index]
            if index > entry_index:
                if (bar.open <= stop) if side == 1 else (bar.open >= stop):
                    exit_price, reason, exit_index = bar.open, "gap_stop", index
                    break
                if channel_exit(bars, index - 1, EXIT_BARS, side):
                    exit_price, reason, exit_index = bar.open, "opposite_channel", index
                    break
                if (bar.low <= stop) if side == 1 else (bar.high >= stop):
                    exit_price, reason, exit_index = stop, "2N_stop", index
                    break
        if exit_price is None:
            exit_index = len(bars) - 1
            exit_price, reason = bars[exit_index].close, "end_of_window"
    gross = (exit_price - entry) * side
    cost = entry * FEE_RATE + exit_price * FEE_RATE
    return {"r_multiple": (gross - cost) / distance if distance else 0.0,
            "exit_reason": reason}


def population_std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def btc_environment(btc: list[Bar], cutoff: datetime) -> dict | None:
    """Frozen environment buckets, using only completed bars at/before cutoff."""
    visible = [b for b in btc if b.timestamp + STEP <= cutoff]
    if len(visible) < VOL_YEAR_BARS + VOL_BARS + 20:
        return None
    closes = [b.close for b in visible]

    def ret(span: int) -> float | None:
        return closes[-1] / closes[-1 - span] - 1 if len(closes) > span else None

    ret_180, ret_540 = ret(TREND_BARS), ret(TREND_LONG_BARS)
    logs = [math.log(b / a) for a, b in zip(closes[-(VOL_BARS + 1):], closes[-VOL_BARS:])]
    vol_now = population_std(logs)
    sample_vols = []
    for offset in range(VOL_BARS, VOL_YEAR_BARS + 1, VOL_BARS):
        end = len(closes) - (VOL_YEAR_BARS - offset)
        if end - VOL_BARS - 1 < 0:
            continue
        seg = [math.log(b / a) for a, b in
               zip(closes[end - VOL_BARS - 1:end - 1], closes[end - VOL_BARS:end])]
        if len(seg) == VOL_BARS:
            sample_vols.append(population_std(seg))
    if len(sample_vols) < 3:
        return None
    vol_median = statistics.median(sample_vols)
    vol_ratio = vol_now / vol_median if vol_median > 0 else None
    hi = max(b.high for b in visible[-TREND_LONG_BARS:])
    drawdown = (hi - closes[-1]) / hi if hi > 0 else None

    trend = "UP" if ret_180 > 0.05 else ("DOWN" if ret_180 < -0.05 else "FLAT")
    trend_long = "UP" if ret_540 > 0.10 else ("DOWN" if ret_540 < -0.10 else "FLAT")
    vol = ("LOW" if vol_ratio < 0.8 else "HIGH" if vol_ratio > 1.2 else "NORMAL") \
        if vol_ratio is not None else None
    dd = ("SHALLOW" if drawdown < 0.05 else "DEEP" if drawdown > 0.20 else "MID") \
        if drawdown is not None else None
    return {"btc_ret_180": round(ret_180, 6), "btc_ret_540": round(ret_540, 6),
            "btc_vol_ratio": round(vol_ratio, 6) if vol_ratio is not None else None,
            "btc_drawdown": round(drawdown, 6) if drawdown is not None else None,
            "btc_trend": trend, "btc_trend_long": trend_long,
            "btc_vol": vol, "btc_drawdown_state": dd}


def build_events() -> list[dict[str, Any]]:
    cache: dict[str, list[Bar]] = {}
    for symbol in SYMBOLS:
        payload = fetch_binance_spot_4h_archive(
            symbol=symbol, start_at=datetime(2021, 6, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 3, tzinfo=timezone.utc))
        cache[symbol] = [Bar.model_validate(x) for x in payload]
        print(f"  loaded {symbol}: {len(cache[symbol])}", flush=True)
    btc = cache["BTCUSDT"]
    btc_index = {b.timestamp: i for i, b in enumerate(btc)}

    events: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        bars = cache[symbol]
        states = causal_direction_states(bars)
        for year in YEARS:
            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            for index in range(max(ENTRY_BARS, ATR_BARS, EXIT_BARS) + 1, len(bars) - 1):
                bar = bars[index]
                if not (start <= bar.timestamp < end):
                    continue
                direction = states[index]
                if direction == "NEUTRAL":
                    continue
                side = 1 if direction == "LONG" else -1
                if breakout(bars, index, ENTRY_BARS) != side:
                    continue
                outcome = simulate_candidate(bars, index + 1, side, atr(bars, index))
                cutoff = bar.timestamp + STEP
                env = btc_environment(btc, cutoff)
                iso = bar.timestamp.isocalendar()
                events.append({
                    "candidate_id": f"{symbol}_{bar.timestamp.isoformat()}",
                    "symbol": symbol, "year": year, "direction": direction,
                    "signal_ts": bar.timestamp.isoformat(),
                    "week_cluster": f"{iso[0]}-W{iso[1]:02d}",
                    "net_R": outcome["r_multiple"], "exit_reason": outcome["exit_reason"],
                    "env_status": "OK" if env else "UNAVAILABLE",
                    "env": env or {},
                })
        print(f"  {symbol}: total events {len(events)}", flush=True)
    return events


def auc_ignore(*_a, **_k):
    return None


def bootstrap_diff(events: list[dict], cell_filter, metric, reps=BOOTSTRAP):
    """Weekly-cluster bootstrap of median(long) - median(short) inside a cell."""
    random.seed(SEED)
    subset = [e for e in events if cell_filter(e)]
    clusters = defaultdict(list)
    for e in subset:
        clusters[e["week_cluster"]].append(e)
    keys = list(clusters)

    def diff(sample: list[dict]) -> float | None:
        longs = [e["net_R"] for e in sample if e["direction"] == "LONG"]
        shorts = [e["net_R"] for e in sample if e["direction"] == "SHORT"]
        if len(longs) < 3 or len(shorts) < 3:
            return None
        return statistics.median(longs) - statistics.median(shorts)

    base = diff(subset)
    if base is None:
        return None
    draws = []
    for _ in range(reps):
        sample = []
        while len(sample) < len(subset) and keys:
            sample.extend(clusters[random.choice(keys)])
        value = diff(sample)
        if value is not None:
            draws.append(value)
    if len(draws) < 100:
        return {"median_diff": round(base, 4), "ci95": None, "reps": len(draws)}
    draws.sort()
    return {"median_diff": round(base, 4),
            "ci95": [round(draws[int(0.025 * len(draws))], 4),
                     round(draws[int(0.975 * len(draws))], 4)],
            "reps": len(draws)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    print("=== loading data and building events ===", flush=True)
    events = build_events()
    usable = [e for e in events if e["env_status"] == "OK"]
    print(f"\ntotal candidates: {len(events)} | with environment: {len(usable)} "
          f"({len(usable)/len(events)*100:.1f}%)", flush=True)

    columns = ["candidate_id", "symbol", "year", "direction", "signal_ts",
               "week_cluster", "net_R", "exit_reason", "env_status",
               "btc_trend", "btc_vol", "btc_trend_long", "btc_drawdown_state",
               "btc_ret_180", "btc_ret_540", "btc_vol_ratio", "btc_drawdown"]
    with open(OUT / "d_events.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for e in usable:
            writer.writerow({**e, **e["env"]})

    report: dict[str, Any] = {"protocol": "S-004.D", "evidence": "EXPLORATORY",
                              "candidates_total": len(events),
                              "with_environment": len(usable),
                              "week_clusters": len({e["week_cluster"] for e in usable}),
                              "net_R_total": round(sum(e["net_R"] for e in usable), 2),
                              "cells": {}, "asymmetry": {}, "per_year": {},
                              "secondary": {}, "portfolio": {}}

    # ---- main table: btc_trend x btc_vol x direction ----
    print("\n=== environment grid: btc_trend x btc_vol x direction ===")
    print(f"{'cell':22s} {'dir':6s} {'n':>5s} {'medianR':>9s} {'sumR':>9s} {'win%':>6s}")
    grid: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for e in usable:
        key = (e["env"]["btc_trend"], e["env"]["btc_vol"])
        grid[key][e["direction"]].append(e["net_R"])
    for key in sorted(grid):
        trend, vol = key
        for direction in ("LONG", "SHORT"):
            values = grid[key][direction]
            if not values:
                continue
            label = f"{trend}/{vol}"
            stats = {"n": len(values), "median_R": round(statistics.median(values), 4),
                     "sum_R": round(sum(values), 2),
                     "win_pct": round(sum(1 for v in values if v > 0) / len(values) * 100, 1)}
            report["cells"].setdefault(label, {})[direction] = stats
            print(f"{label:22s} {direction:6s} {len(values):5d} {stats['median_R']:9.3f} "
                  f"{stats['sum_R']:9.1f} {stats['win_pct']:5.1f}%")

    # ---- direction asymmetry with clustered CI ----
    print("\n=== long-minus-short asymmetry (weekly-cluster bootstrap) ===")
    print(f"{'cell':22s} {'n_L':>4s} {'n_S':>4s} {'medianL-medianS':>16s} {'95% CI':>18s} judgement")
    for key in sorted(grid):
        trend, vol = key
        longs, shorts = grid[key]["LONG"], grid[key]["SHORT"]
        if len(longs) < 3 or len(shorts) < 3:
            continue
        result = bootstrap_diff(usable, lambda e, t=trend, v=vol:
                                e["env"]["btc_trend"] == t and e["env"]["btc_vol"] == v, "median")
        label = f"{trend}/{vol}"
        ci = result.get("ci95") if result else None
        verdict = "n/a"
        if ci:
            verdict = "excludes 0" if (ci[0] > 0 or ci[1] < 0) else "includes 0"
        report["asymmetry"][label] = {**(result or {}),
                                      "n_long": len(longs), "n_short": len(shorts),
                                      "verdict": verdict}
        print(f"{label:22s} {len(longs):4d} {len(shorts):4d} "
              f"{result['median_diff'] if result else float('nan'):16.3f} "
              f"{str(ci):>18s} {verdict}")

    # ---- year stability ----
    print("\n=== year-by-year: median R by direction within btc_trend ===")
    for trend in ("UP", "FLAT", "DOWN"):
        print(f"  trend={trend}")
        for year in YEARS:
            row = {}
            for direction in ("LONG", "SHORT"):
                values = [e["net_R"] for e in usable
                          if e["year"] == year and e["env"]["btc_trend"] == trend
                          and e["direction"] == direction]
                row[direction] = (len(values),
                                  round(statistics.median(values), 3) if values else None,
                                  round(sum(values), 1))
            report["per_year"].setdefault(trend, {})[str(year)] = row
            print(f"    {year}: LONG n={row['LONG'][0]:3d} med={row['LONG'][1]} sum={row['LONG'][2]} | "
                  f"SHORT n={row['SHORT'][0]:3d} med={row['SHORT'][1]} sum={row['SHORT'][2]}")

    # ---- secondary strata ----
    print("\n=== secondary: btc_trend_long and btc_drawdown_state ===")
    for feature in ("btc_trend_long", "btc_drawdown_state"):
        print(f"  {feature}")
        for bucket in sorted({e["env"][feature] for e in usable}):
            for direction in ("LONG", "SHORT"):
                values = [e["net_R"] for e in usable
                          if e["env"][feature] == bucket and e["direction"] == direction]
                if not values:
                    continue
                stats = {"n": len(values), "median_R": round(statistics.median(values), 4),
                         "sum_R": round(sum(values), 2)}
                report["secondary"].setdefault(feature, {}).setdefault(bucket, {})[direction] = stats
                print(f"    {bucket:8s} {direction:6s} n={len(values):4d} "
                      f"med={stats['median_R']:+7.3f} sum={stats['sum_R']:+8.1f}")

    # ---- portfolio layer ----
    print("\n=== portfolio layer: is the profit one market episode? ===")
    week_r: dict[str, float] = defaultdict(float)
    for e in usable:
        week_r[e["week_cluster"]] += e["net_R"]
    weeks_sorted = sorted(week_r.values(), reverse=True)
    total = sum(week_r.values())
    top5 = sum(weeks_sorted[:5])
    report["portfolio"] = {
        "weeks": len(week_r), "total_R": round(total, 2),
        "top5_week_R": round(top5, 2),
        "top5_share_pct": round(top5 / total * 100, 1) if total else None,
        "positive_weeks": sum(1 for v in week_r.values() if v > 0),
        "positive_week_pct": round(sum(1 for v in week_r.values() if v > 0)
                                   / len(week_r) * 100, 1),
    }
    print(f"  weeks={len(week_r)} totalR={total:.1f} top5weeks={top5:.1f} "
          f"({top5/total*100:.1f}%) positive_weeks={report['portfolio']['positive_week_pct']}%")

    symbol_week: dict[str, dict[str, float]] = defaultdict(dict)
    for e in usable:
        symbol_week[e["symbol"]][e["week_cluster"]] = (
            symbol_week[e["symbol"]].get(e["week_cluster"], 0.0) + e["net_R"])
    shared = sorted({w for m in symbol_week.values() for w in m})
    matrix = {s: [symbol_week[s].get(w, 0.0) for w in shared] for s in symbol_week}
    pairs = []
    symbols = sorted(matrix)
    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            va, vb = matrix[a], matrix[b]
            ma, mb = statistics.fmean(va), statistics.fmean(vb)
            num = sum((x - ma) * (y - mb) for x, y in zip(va, vb))
            da = math.sqrt(sum((x - ma) ** 2 for x in va))
            db = math.sqrt(sum((y - mb) ** 2 for y in vb))
            if da and db:
                pairs.append({"pair": f"{a[:3]}/{b[:3]}", "corr": round(num / (da * db), 3)})
    pairs.sort(key=lambda p: -p["corr"])
    mean_corr = round(statistics.fmean(p["corr"] for p in pairs), 3) if pairs else None
    report["portfolio"]["mean_symbol_week_corr"] = mean_corr
    report["portfolio"]["top_correlated"] = pairs[:5]
    print(f"  mean cross-symbol weekly-R correlation = {mean_corr}")
    print("  highest: " + ", ".join(f"{p['pair']} {p['corr']:+.2f}" for p in pairs[:5]))

    report["exit_reason_counts"] = dict(Counter(e["exit_reason"] for e in usable))
    (OUT / "d_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\nwritten {OUT/'d_events.csv'} and {OUT/'d_report.json'}")


if __name__ == "__main__":
    main()
