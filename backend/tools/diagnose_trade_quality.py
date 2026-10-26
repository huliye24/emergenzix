"""Diagnose WHY the NEMT breakout strategy earns little, using existing trades.

Runs on the AWS host where the Binance archive cache and run JSONs live.

Question 1 — is the 2N stop too tight?
    For every trade, measure how far price continued in the ORIGINAL direction
    after the exit. If a large share travels >= 1R / 2R after we are stopped out,
    the stop is harvesting noise and the entry was right.

Question 2 — how much of the move did we capture while in?
    MFE (max favourable excursion) during the holding period, in R units, and
    the capture ratio = realised move / MFE.

Question 3 — where does the money actually come from?
    Outcome distribution in R units by exit reason.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
from collections import defaultdict

RUN_DIR = pathlib.Path("/home/ubuntu/nemt-xsec/out")
TRADES = pathlib.Path("/home/ubuntu/nemt-xsec/xsec_payload.json")
TARGET = pathlib.Path("/home/ubuntu/nemt-xsec/diagnosis.json")

HORIZONS = (12, 42, 126, 252)   # 4h bars: 2d, 1w, 3w, 6w


def load_bars():
    """Bars come from the same verified loader the backtest used."""
    import os
    sys.path.insert(0, "/home/ubuntu/nemt-xsec/backend")
    os.environ.setdefault("BINANCE_ARCHIVE_CACHE_DIR", "/home/ubuntu/nemt-xsec/cache")
    from datetime import datetime, timedelta, timezone
    from emergenzix_api.historical_data import fetch_binance_spot_4h_archive
    from emergenzix_api.schemas import Bar

    cache: dict[str, list] = {}
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
               "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
    for sym in symbols:
        payload = fetch_binance_spot_4h_archive(
            symbol=sym,
            start_at=datetime(2021, 9, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        cache[sym] = [Bar.model_validate(x) for x in payload]
        print(f"  bars {sym}: {len(cache[sym])}", flush=True)
    return cache


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(TRADES.read_text(encoding="utf-8"))
    # Baseline cell only, 3x leverage, 1% risk: the unique signal set.
    base = [t for t in payload["trades"]
            if t["cell"] == "R0" and t["max_leverage"] == 3.0 and t["risk_pct"] == 1.0]
    print(f"R0 trades at 1% risk: {len(base)}", flush=True)

    bars = load_bars()
    index = {sym: {bar.timestamp: i for i, bar in enumerate(seq)} for sym, seq in bars.items()}

    rows = []
    for t in base:
        seq = bars[t["symbol"]]
        stamp = t["exit_at"]
        from datetime import datetime
        exit_dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        exit_i = index[t["symbol"]].get(exit_dt)
        if exit_i is None:
            continue
        side = 1 if t["side"] == "LONG" else -1
        unit_risk = t["risk_usdt"] / t["quantity"] if t["quantity"] else None
        if not unit_risk:
            continue
        entry_i = t["holding_bars"] and exit_i - t["holding_bars"]
        if entry_i is None or entry_i < 0:
            continue

        # MFE / MAE during the holding period, in R units of price.
        mfe = mae = 0.0
        for k in range(entry_i, exit_i + 1):
            fav = side * (seq[k].high - t["avg_entry"]) if side == 1 else side * (seq[k].low - t["avg_entry"])
            adv = side * (seq[k].low - t["avg_entry"]) if side == 1 else side * (seq[k].high - t["avg_entry"])
            mfe = max(mfe, fav / unit_risk)
            mae = min(mae, adv / unit_risk)

        realised_r = side * (t["exit_price"] - t["avg_entry"]) / unit_risk

        # Post-exit continuation in the original direction.
        continuation = {}
        for horizon in HORIZONS:
            best = 0.0
            for k in range(exit_i + 1, min(exit_i + 1 + horizon, len(seq))):
                fav = side * (seq[k].high - t["exit_price"]) if side == 1 else side * (seq[k].low - t["exit_price"])
                best = max(best, fav / unit_risk)
            continuation[str(horizon)] = best

        rows.append({
            "symbol": t["symbol"], "year": t["year"], "side": t["side"],
            "exit_reason": t["exit_reason"], "r_multiple": t["r_multiple"],
            "realised_r": realised_r, "mfe_r": mfe, "mae_r": mae,
            "capture_ratio": (realised_r / mfe) if mfe > 0.05 else None,
            "holding_bars": t["holding_bars"],
            **{f"cont_{h}": v for h, v in continuation.items()},
        })
    print(f"analysed: {len(rows)}", flush=True)

    def pct(values, q):
        values = sorted(values)
        if not values:
            return None
        k = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
        return round(values[k], 3)

    out = {"trades_analysed": len(rows), "horizons_4h_bars": list(HORIZONS)}

    # ---- Q1: post-exit continuation, overall and by exit reason ----
    by_reason = defaultdict(list)
    for row in rows:
        by_reason[row["exit_reason"]].append(row)
    q1 = {}
    for reason, items in sorted(by_reason.items()):
        entry = {"count": len(items)}
        for h in HORIZONS:
            vals = [r[f"cont_{h}"] for r in items]
            entry[f"cont_{h}_median_R"] = pct(vals, 0.5)
            entry[f"cont_{h}_pct_ge_1R"] = round(sum(1 for v in vals if v >= 1.0) / len(vals) * 100, 1)
            entry[f"cont_{h}_pct_ge_2R"] = round(sum(1 for v in vals if v >= 2.0) / len(vals) * 100, 1)
            entry[f"cont_{h}_pct_ge_3R"] = round(sum(1 for v in vals if v >= 3.0) / len(vals) * 100, 1)
        q1[reason] = entry
    out["q1_post_exit_continuation"] = q1

    # ---- Q2: MFE capture ----
    caps = [r["capture_ratio"] for r in rows if r["capture_ratio"] is not None]
    mfes = [r["mfe_r"] for r in rows]
    out["q2_capture"] = {
        "trades_with_meaningful_mfe": len(caps),
        "mfe_median_R": pct(mfes, 0.5),
        "mfe_p75_R": pct(mfes, 0.75),
        "mfe_p90_R": pct(mfes, 0.90),
        "capture_ratio_median": pct(caps, 0.5),
        "capture_ratio_mean": round(statistics.fmean(caps), 3) if caps else None,
        "pct_trades_capture_below_30pct": round(
            sum(1 for c in caps if c < 0.30) / len(caps) * 100, 1) if caps else None,
        "pct_trades_mfe_ge_1R": round(sum(1 for m in mfes if m >= 1.0) / len(mfes) * 100, 1),
        "pct_trades_mfe_ge_2R": round(sum(1 for m in mfes if m >= 2.0) / len(mfes) * 100, 1),
        "pct_trades_mfe_ge_3R": round(sum(1 for m in mfes if m >= 3.0) / len(mfes) * 100, 1),
    }

    # ---- Q3: outcome distribution by exit reason ----
    q3 = {}
    for reason, items in sorted(by_reason.items()):
        rs = [r["r_multiple"] for r in items]
        q3[reason] = {
            "count": len(items),
            "sum_R": round(sum(rs), 2),
            "median_R": pct(rs, 0.5),
            "mean_R": round(statistics.fmean(rs), 3),
            "pct_negative": round(sum(1 for v in rs if v <= 0) / len(rs) * 100, 1),
        }
    out["q3_by_exit_reason"] = q3

    # ---- Q4: what the average trade looks like ----
    allr = [r["realised_r"] for r in rows]
    out["q4_trade_shape"] = {
        "realised_median_R": pct(allr, 0.5),
        "realised_mean_R": round(statistics.fmean(allr), 3),
        "mae_median_R": pct([r["mae_r"] for r in rows], 0.5),
        "pct_trades_never_reached_1R": round(
            sum(1 for r in rows if r["mfe_r"] < 1.0) / len(rows) * 100, 1),
    }

    TARGET.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
