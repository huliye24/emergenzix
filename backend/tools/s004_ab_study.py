"""S-004.A.2 — event-level A/B study of the sparse-trend filter (research only).

Design note (this is the whole point of the module):

    The backtest's realised trades are NOT a valid sample for evaluating a filter,
    because position occupancy means a rejected signal frees the account for a
    later signal.  So the gate and execution must be decoupled.

This module therefore enumerates EVERY Donchian+NEMT candidate (allowing the
hypothetical trades to overlap), simulates each one independently to its own exit
under the unchanged entry/exit/cost rules, and pairs that outcome with the
S-004.A verdict taken at the same decision bar.

Outputs, per configuration:
  * candidate count, permit rate
  * mean / median net R for accepted vs rejected vs all
  * how many of the top 1% / 5% / 10% winners the filter rejected
  * fixed-fractional equity for "take all" vs "take accepted only"
  * total return and max drawdown for both
"""

from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/ubuntu/nemt-s004/backend")

from emergenzix_api.historical_data import fetch_binance_spot_4h_archive  # noqa: E402
from emergenzix_api.nemt_strategy.nemt_momentum_v2 import compute_momentum_state  # noqa: E402
from emergenzix_api.nemt_strategy.s004_trend_classifier import (  # noqa: E402
    CANDIDATE, CONFIGS, classify,
)
from emergenzix_api.nemt_strategy.v522_ablation_cli import (  # noqa: E402
    ATR_BARS, FEE_RATE, SLIPPAGE_RATE, STOP_MULTIPLE, _direction, atr, breakout,
    channel_exit,
)
from emergenzix_api.schemas import Bar  # noqa: E402

ENTRY_BARS = 120
EXIT_BARS = 60
WARMUP_DAYS = 420
RISK = 0.01
TARGET = pathlib.Path("/home/ubuntu/nemt-s004/ab_study.json")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
           "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
YEARS = [2022, 2023, 2024, 2025]
CONFIG_KEYS = ["A1", "A2", "A3", "A4", "A5"]


def simulate_candidate(bars: list[Bar], entry_index: int, side: int,
                       n_value: float) -> dict | None:
    """Independently simulate one candidate trade from `entry_index` (next open)."""
    entry = bars[entry_index].open * (1 + side * SLIPPAGE_RATE)
    distance = max(STOP_MULTIPLE * n_value, entry * 0.01)
    stop = entry - side * distance
    if (entry_index < len(bars) and
            (bars[entry_index].low <= stop if side == 1 else bars[entry_index].high >= stop)):
        # entry-bar stop is active
        exit_price, reason, exit_index = stop, "entry_bar_stop", entry_index
    else:
        exit_price = reason = exit_index = None
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
    net_per_unit = gross - cost
    r_multiple = net_per_unit / distance if distance else 0.0
    return {"entry_price": entry, "exit_price": exit_price, "exit_reason": reason,
            "holding_bars": exit_index - entry_index, "r_multiple": r_multiple}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    zone = timezone.utc
    records: dict[str, list] = {key: [] for key in CONFIG_KEYS}
    all_outcomes = []

    for symbol in SYMBOLS:
        for year in YEARS:
            start = datetime(year, 1, 1, tzinfo=zone)
            end = datetime(year + 1, 1, 1, tzinfo=zone)
            payload = fetch_binance_spot_4h_archive(
                symbol=symbol,
                start_at=start - timedelta(days=WARMUP_DAYS),
                end_at=end + timedelta(hours=4))
            bars = [Bar.model_validate(x) for x in payload]
            closes = [b.close for b in bars]
            warmup = max(ENTRY_BARS, ATR_BARS, EXIT_BARS) + 1

            for index in range(warmup, len(bars) - 1):
                bar = bars[index]
                if not (start <= bar.timestamp < end):
                    continue
                state = compute_momentum_state(closes[:index + 1])
                direction = _direction(state)
                if not direction:
                    continue
                if breakout(bars, index, ENTRY_BARS) != direction:
                    continue
                outcome = simulate_candidate(bars, index + 1, direction, atr(bars, index))
                if outcome is None:
                    continue
                base = {"symbol": symbol, "year": year, "side": direction,
                        "decision_ts": bar.timestamp.isoformat(), **outcome}
                all_outcomes.append(base)
                as_of = bar.timestamp + timedelta(hours=4)
                for key in CONFIG_KEYS:
                    verdict = classify(bars[:index + 1], as_of=as_of,
                                       direction=direction, config=CONFIGS[key])
                    records[key].append({**base, "status": verdict.status,
                                         "reason": verdict.reason})
            print(f"  {symbol} {year}: cumulative candidates {len(all_outcomes)}", flush=True)

    print(f"total candidates: {len(all_outcomes)}")
    result = {"candidates_total": len(all_outcomes), "configs": {}}
    base_rs = [x["r_multiple"] for x in all_outcomes]
    base_sum = sum(base_rs)
    ordered = sorted(base_rs, reverse=True)
    cuts = {}
    for label, pct in (("top1", 0.01), ("top5", 0.05), ("top10", 0.10)):
        k = max(1, int(round(len(ordered) * pct)))
        cuts[label] = set(id(x) for x in [])   # placeholder
        cuts[label] = k, ordered[:k]

    print()
    print(f"{'cfg':4s} {'cand':>6s} {'permit%':>8s} {'meanR_all':>10s} {'meanR_acc':>10s} "
          f"{'meanR_rej':>10s} {'sumR_acc':>9s} {'top1 rej':>9s} {'top5 rej':>9s} {'top10 rej':>10s}")
    for key in CONFIG_KEYS:
        rows = records[key]
        accepted = [r for r in rows if r["status"] == CANDIDATE]
        # a candidate is "rejected" by the filter when the verdict is not CANDIDATE
        rejected = [r for r in rows if r["status"] != CANDIDATE]
        acc_rs = [r["r_multiple"] for r in accepted]
        rej_rs = [r["r_multiple"] for r in rejected]
        # winner retention: match by (symbol, year, decision_ts)
        entry = {
            "candidates": len(rows),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "permit_rate_pct": round(len(accepted) / len(rows) * 100, 2) if rows else 0,
            "mean_R_all": round(statistics.fmean(base_rs), 4),
            "mean_R_accepted": round(statistics.fmean(acc_rs), 4) if acc_rs else None,
            "mean_R_rejected": round(statistics.fmean(rej_rs), 4) if rej_rs else None,
            "median_R_accepted": round(statistics.median(acc_rs), 4) if acc_rs else None,
            "sum_R_accepted": round(sum(acc_rs), 2),
            "sum_R_rejected": round(sum(rej_rs), 2),
            "R_retained_pct": round(sum(acc_rs) / base_sum * 100, 1) if base_sum else None,
        }
        accepted_keys = {(r["symbol"], r["year"], r["decision_ts"]) for r in accepted}
        top_by_key = {}
        for row in all_outcomes:
            top_by_key[(row["symbol"], row["year"], row["decision_ts"])] = row["r_multiple"]
        for label, (k, _) in cuts.items():
            # the k largest candidates, identified by key
            ranked = sorted(top_by_key.items(), key=lambda kv: -kv[1])[:k]
            kept = sum(1 for key_, _ in ranked if key_ in accepted_keys)
            entry[f"{label}_kept"] = kept
            entry[f"{label}_total"] = k
            entry[f"{label}_r_sum"] = round(sum(v for _, v in ranked), 2)
        result["configs"][key] = entry
        print(f"{key:4s} {len(rows):6d} {entry['permit_rate_pct']:7.1f}% "
              f"{entry['mean_R_all']:+10.3f} {entry['mean_R_accepted']:+10.3f} "
              f"{entry['mean_R_rejected']:+10.3f} {entry['sum_R_accepted']:+9.2f} "
              f"{entry['top1_kept']}/{entry['top1_total']:<6d} "
              f"{entry['top5_kept']}/{entry['top5_total']:<6d} "
              f"{entry['top10_kept']}/{entry['top10_total']:<7d}")

    # ---------- equity comparison: take-all vs take-accepted ----------
    print()
    print("=== fixed-fractional equity (chronological, 1% risk) ===")
    ordered_rows = sorted(all_outcomes, key=lambda r: r["decision_ts"])
    for key in ("A1", "A2", "A4"):
        accepted_keys = {(r["symbol"], r["year"], r["decision_ts"])
                         for r in records[key] if r["status"] == CANDIDATE}
        def equity_curve(rows):
            equity, peak, worst = 1.0, 1.0, 0.0
            for row in rows:
                equity *= (1 + RISK * row["r_multiple"])
                peak = max(peak, equity)
                worst = max(worst, (peak - equity) / peak)
            return equity, worst
        all_eq = equity_curve(ordered_rows)
        acc_rows = [r for r in ordered_rows
                    if (r["symbol"], r["year"], r["decision_ts"]) in accepted_keys]
        acc_eq = equity_curve(acc_rows)
        result["configs"][key]["equity_all"] = {"final": round(all_eq[0], 4),
                                               "max_dd_pct": round(all_eq[1] * 100, 1)}
        result["configs"][key]["equity_accepted"] = {"final": round(acc_eq[0], 4),
                                                    "max_dd_pct": round(acc_eq[1] * 100, 1),
                                                    "trades": len(acc_rows)}
        print(f"  {key}: take-all x{all_eq[0]:.3f} (dd {all_eq[1]*100:.1f}%)   "
              f"accepted-only x{acc_eq[0]:.3f} (dd {acc_eq[1]*100:.1f}%, {len(acc_rows)} trades)")

    result["exit_reason_mix"] = dict(Counter(r["exit_reason"] for r in all_outcomes))
    result["base_R_sum"] = round(base_sum, 2)
    TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {TARGET}")


if __name__ == "__main__":
    main()
