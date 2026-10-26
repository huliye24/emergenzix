"""S-004.A.3.2 — event-level feature dataset for the 2,356 historical candidates.

Links the merged causal feature extractor (``early_trend_features``) and the
pinned per-bar state exporter (``s004a3_state``) to the SAME candidate set used
in A.2, then compares pre-entry features across three outcome groups:

    top1     the 24 candidates with the largest net R  (top 1% of 2,356)
    other+   positive net R, excluding top1
    nonpos   net R <= 0

Design constraints taken from the A.3 protocol:
  * features use only bars with ``t_open + 4h <= decision cutoff`` (no future)
  * outcomes are ANALYSIS LABELS only; they never enter feature computation
  * uncertainty is estimated with a WEEKLY BLOCK bootstrap, because candidates
    inside one market week are not independent observations
  * no threshold is fitted and no trading rule is produced

Outputs: ``a3_events.csv`` (event level), ``a3_report.json`` (all statistics).
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

sys.path.insert(0, "/home/ubuntu/nemt-a3/backend")

from emergenzix_api.early_trend_features import extract_early_trend_features  # noqa: E402
from emergenzix_api.historical_data import fetch_binance_spot_4h_archive  # noqa: E402
from emergenzix_api.nemt_strategy.s004a3_state import causal_direction_states  # noqa: E402
from emergenzix_api.nemt_strategy.v522_ablation_cli import (  # noqa: E402
    ATR_BARS, FEE_RATE, SLIPPAGE_RATE, STOP_MULTIPLE, _direction, atr, breakout,
    channel_exit,
)
from emergenzix_api.schemas import Bar  # noqa: E402

ENTRY_BARS, EXIT_BARS, WARMUP_DAYS = 120, 60, 420
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
YEARS = [2022, 2023, 2024, 2025]
FEATURES = ["trend_age_bars", "efficiency_42", "vol_ratio_42_126", "range_position_126"]
OUT = pathlib.Path("/home/ubuntu/nemt-a3")
SEED, BOOTSTRAP = 20261026, 2000


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
            "exit_reason": reason, "holding_bars": exit_index - entry_index,
            "exit_ts": bars[exit_index].timestamp.isoformat()}


def build_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for year in YEARS:
            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            payload = fetch_binance_spot_4h_archive(
                symbol=symbol, start_at=start - timedelta(days=WARMUP_DAYS),
                end_at=end + timedelta(hours=4))
            bars = [Bar.model_validate(x) for x in payload]
            states = causal_direction_states(bars)
            warmup = max(ENTRY_BARS, ATR_BARS, EXIT_BARS) + 1
            count = 0
            for index in range(warmup, len(bars) - 1):
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
                cutoff = bar.timestamp + timedelta(hours=4)
                features = extract_early_trend_features(
                    bars[:index + 1], as_of=cutoff, direction=direction,
                    directional_states=states[:index + 1])
                iso = bar.timestamp.isocalendar()
                events.append({
                    "candidate_id": f"{symbol}_{bar.timestamp.isoformat()}",
                    "symbol": symbol, "year": year, "direction": direction,
                    "signal_ts": bar.timestamp.isoformat(),
                    "feature_cutoff_utc": features.feature_cutoff_utc,
                    "week_cluster": f"{iso[0]}-W{iso[1]:02d}",
                    "net_R": outcome["r_multiple"],
                    "exit_reason": outcome["exit_reason"],
                    "holding_bars": outcome["holding_bars"],
                    "exit_ts": outcome["exit_ts"],
                    "feature_status": features.feature_status,
                    "feature_reason": features.reason,
                    **{name: getattr(features, name) for name in FEATURES},
                })
                count += 1
            print(f"  {symbol} {year}: {count} candidates (total {len(events)})", flush=True)
    return events


def auc(values: list[float], labels: list[int]) -> float | None:
    """Rank-based AUC (Mann-Whitney); None when a class is empty."""
    n1 = sum(labels)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return None
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    rank_sum = sum(r for r, l in zip(ranks, labels) if l == 1)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def quartiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"q1": None, "median": None, "q3": None, "mean": None}
    values = sorted(values)
    def q(p: float) -> float:
        k = p * (len(values) - 1)
        lo, hi = math.floor(k), math.ceil(k)
        return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (k - lo)
    return {"q1": round(q(0.25), 6), "median": round(q(0.5), 6),
            "q3": round(q(0.75), 6), "mean": round(statistics.fmean(values), 6)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    print("=== enumerating candidates (identical to A.2) ===", flush=True)
    events = build_events()
    total = len(events)
    print(f"total candidates: {total}", flush=True)

    # ---- outcome groups (labels only, never features) ----
    ranked = sorted(events, key=lambda e: -e["net_R"])
    for label, pct in (("top1", 0.01), ("top5", 0.05), ("top10", 0.10)):
        k = max(1, int(round(total * pct)))
        for event in ranked[:k]:
            event.setdefault("tags", []).append(label)
    for event in events:
        event["tags"] = event.get("tags", [])
        r = event["net_R"]
        event["group"] = ("top1" if "top1" in event["tags"]
                          else ("other_pos" if r > 0 else "nonpos"))

    # ---- event-level CSV ----
    columns = ["candidate_id", "symbol", "year", "direction", "signal_ts",
               "week_cluster", "net_R", "group", "feature_status", "feature_reason",
               "exit_reason", "holding_bars", "exit_ts", "feature_cutoff_utc"] + FEATURES
    with open(OUT / "a3_events.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            writer.writerow(event)

    # ---- group statistics ----
    groups = {name: [e for e in events if e["group"] == name]
              for name in ("top1", "other_pos", "nonpos")}
    report: dict[str, Any] = {
        "protocol": "S-004.A.3.2", "evidence": "EXPLORATORY",
        "candidates_total": total,
        "group_sizes": {k: len(v) for k, v in groups.items()},
        "week_clusters": len({e["week_cluster"] for e in events}),
        "net_R_total": round(sum(e["net_R"] for e in events), 2),
        "groups": {}, "auc": {}, "stratified": {}, "sensitivity": {},
        "bootstrap": {},
    }

    print("\n=== group feature statistics ===")
    print(f"{'feature':20s} {'group':10s} {'n':>5s} {'miss':>5s} {'q1':>9s} {'median':>9s} {'q3':>9s}")
    for feature in FEATURES:
        report["groups"][feature] = {}
        for name, rows in groups.items():
            values = [e[feature] for e in rows if e[feature] is not None]
            missing = len(rows) - len(values)
            stats = quartiles(values)
            report["groups"][feature][name] = {"n": len(rows), "missing": missing,
                                              "missing_pct": round(missing / len(rows) * 100, 2) if rows else None,
                                              **stats}
            print(f"{feature:20s} {name:10s} {len(rows):5d} {missing:5d} "
                  f"{stats['q1'] if stats['q1'] is not None else -1:9.3f} "
                  f"{stats['median'] if stats['median'] is not None else -1:9.3f} "
                  f"{stats['q3'] if stats['q3'] is not None else -1:9.3f}")

    # ---- AUC: top1 vs everything else, and feature vs direction (collinearity) ----
    print("\n=== discrimination (AUC) ===")
    for feature in FEATURES:
        usable = [e for e in events if e[feature] is not None]
        if len(usable) < 50:
            continue
        labels_top1 = [1 if e["group"] == "top1" else 0 for e in usable]
        values = [float(e[feature]) for e in usable]
        a_top1 = auc(values, labels_top1)
        labels_long = [1 if e["direction"] == "LONG" else 0 for e in usable]
        a_dir = auc(values, labels_long)
        for label, pct in (("top5", 0.05), ("top10", 0.10)):
            labels = [1 if label in e["tags"] else 0 for e in usable]
            report["sensitivity"].setdefault(label, {})[feature] = round(auc(values, labels), 4) if auc(values, labels) is not None else None
        report["auc"][feature] = {"top1_vs_rest": round(a_top1, 4) if a_top1 is not None else None,
                                  "direction_collinearity": round(a_dir, 4) if a_dir is not None else None}
        print(f"  {feature:20s} top1 vs rest = {a_top1:5.3f}   |  vs direction = {a_dir:5.3f}")

    # ---- weekly block bootstrap ----
    random.seed(SEED)
    clusters: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        clusters[event["week_cluster"]].append(event)
    keys = list(clusters)
    print(f"\n=== weekly block bootstrap ({BOOTSTRAP} reps, {len(keys)} clusters) ===")
    for feature in FEATURES:
        usable = [e for e in events if e[feature] is not None]
        base_values = [float(e[feature]) for e in usable]
        base_labels = [1 if e["group"] == "top1" else 0 for e in usable]
        base_auc = auc(base_values, base_labels)
        if base_auc is None:
            continue
        draws = []
        for _ in range(BOOTSTRAP):
            sample: list[dict] = []
            while len(sample) < len(usable):
                sample.extend(clusters[random.choice(keys)])
            sample = [e for e in sample if e[feature] is not None][:len(usable)]
            labels = [1 if e["group"] == "top1" else 0 for e in sample]
            if sum(labels) == 0 or sum(labels) == len(labels):
                continue
            value = auc([float(e[feature]) for e in sample], labels)
            if value is not None:
                draws.append(value)
        draws.sort()
        low = draws[int(0.025 * len(draws))]
        high = draws[int(0.975 * len(draws))]
        report["bootstrap"][feature] = {"auc": round(base_auc, 4),
                                        "ci95": [round(low, 4), round(high, 4)],
                                        "excludes_050": bool(low > 0.5 or high < 0.5),
                                        "reps_used": len(draws)}
        print(f"  {feature:20s} AUC {base_auc:5.3f}  95% CI [{low:5.3f}, {high:5.3f}]  "
              f"{'** excludes 0.5' if (low > 0.5 or high < 0.5) else 'includes 0.5'}")

    # ---- stratification ----
    print("\n=== stratified median (top1 vs others) ===")
    for feature in FEATURES:
        entry: dict[str, Any] = {}
        for key in ("direction", "year"):
            entry[key] = {}
            for bucket in sorted({e[key] for e in events}):
                top = [e[feature] for e in events if e[key] == bucket and e["group"] == "top1" and e[feature] is not None]
                rest = [e[feature] for e in events if e[key] == bucket and e["group"] != "top1" and e[feature] is not None]
                entry[key][str(bucket)] = {
                    "top1_n": len(top), "rest_n": len(rest),
                    "top1_median": round(statistics.median(top), 4) if top else None,
                    "rest_median": round(statistics.median(rest), 4) if rest else None,
                }
        top = [e[feature] for e in events if e["group"] == "top1" and e[feature] is not None]
        rest = [e[feature] for e in events if e["group"] != "top1" and e[feature] is not None]
        entry["overall"] = {"top1_median": round(statistics.median(top), 4) if top else None,
                            "rest_median": round(statistics.median(rest), 4) if rest else None}
        report["stratified"][feature] = entry
        print(f"  {feature:20s} top1 {entry['overall']['top1_median']} vs rest {entry['overall']['rest_median']}")

    # ---- single-event influence: drop the largest winner ----
    biggest = ranked[0]
    print(f"\n=== single-event influence: dropping the largest winner ({biggest['candidate_id']}, R={biggest['net_R']:.1f}) ===")
    report["sensitivity"]["drop_largest_winner"] = {}
    for feature in FEATURES:
        reduced = [e for e in events if e is not biggest and e[feature] is not None]
        labels = [1 if e["group"] == "top1" else 0 for e in reduced]
        value = auc([float(e[feature]) for e in reduced], labels)
        report["sensitivity"]["drop_largest_winner"][feature] = round(value, 4) if value is not None else None
        print(f"  {feature:20s} AUC without it = {value:5.3f}")

    report["exit_reason_counts"] = dict(Counter(e["exit_reason"] for e in events))
    report["feature_status_counts"] = dict(Counter(e["feature_status"] for e in events))
    (OUT / "a3_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"\nwritten {OUT/'a3_events.csv'} and {OUT/'a3_report.json'}")


if __name__ == "__main__":
    main()
