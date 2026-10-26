"""Dataset expansion audit: verify a multi-symbol 4h universe is usable.

Purpose (user request: unlock the data interface and widen the dataset):

* fetch any Binance spot symbol through the verified-archive loader;
* reuse the on-disk cache so a wider universe is not re-downloaded each run;
* report, per symbol, bar count, coverage gaps, price range and a content
  fingerprint, so a strategy run can only use symbols with complete data.

Research only. No order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import (
    HistoricalArchiveUnavailable, cache_directory, fetch_binance_spot_4h_archive,
)
from .registry import data_fingerprint
from emergenzix_api.schemas import Bar

DEFAULT_UNIVERSE = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
)
BARS_PER_DAY = 6
LOOKBACK_DAYS = 120


def audit_symbol(*, symbol: str, start: datetime, end: datetime) -> dict:
    """Fetch one symbol and summarise coverage; never raises on missing data."""
    try:
        payloads = fetch_binance_spot_4h_archive(
            symbol=symbol,
            start_at=start.astimezone(timezone.utc) - timedelta(days=LOOKBACK_DAYS),
            end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
        )
    except (HistoricalArchiveUnavailable, ValueError, OSError) as exc:
        return {"symbol": symbol, "status": "UNAVAILABLE",
                "reason": type(exc).__name__}

    bars = [Bar.model_validate(item) for item in payloads]
    inside = [bar for bar in bars
              if start <= bar.timestamp.astimezone(timezone.utc) < end]
    if not inside:
        return {"symbol": symbol, "status": "EMPTY_IN_WINDOW"}

    expected = int((end - start).total_seconds() // (4 * 3600))
    closes = [bar.close for bar in inside]
    gaps = expected - len(inside)
    return {
        "symbol": symbol,
        "status": "OK" if gaps <= 0 else "GAPPED",
        "bars_total": len(bars),
        "bars_in_window": len(inside),
        "bars_expected": expected,
        "missing_bars": max(0, gaps),
        "coverage_pct": round(len(inside) / expected * 100, 4) if expected else 0.0,
        "first_bar": inside[0].timestamp.isoformat(),
        "last_bar": inside[-1].timestamp.isoformat(),
        "price_first": inside[0].close,
        "price_last": inside[-1].close,
        "price_min": min(bar.low for bar in inside),
        "price_max": max(bar.high for bar in inside),
        "buy_and_hold_pct": round((closes[-1] / closes[0] - 1) * 100, 4),
        "data_fingerprint": data_fingerprint([bar.model_dump() for bar in bars]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit a multi-symbol Binance 4h universe (research only)")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    universe = ([token.strip().upper() for token in args.symbols.split(",") if token.strip()]
                if args.symbols else list(DEFAULT_UNIVERSE))
    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)

    results = []
    for symbol in universe:
        result = audit_symbol(symbol=symbol, start=start, end=end)
        results.append(result)
        flag = result["status"]
        if flag == "OK":
            print(f"  {symbol:10s} {flag:6s} bars={result['bars_in_window']:5d} "
                  f"cov={result['coverage_pct']:7.3f}%  buy&hold={result['buy_and_hold_pct']:+9.2f}%  "
                  f"fp={result['data_fingerprint'][:16]}")
        else:
            print(f"  {symbol:10s} {flag:6s} {result.get('reason', '')}")

    usable = [item for item in results if item["status"] == "OK"]
    report = {
        "protocol": "binance-multi-symbol-universe-audit-001",
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "cache_dir": str(cache_directory()) if cache_directory() else None,
        "universe_requested": universe,
        "symbols_usable": [item["symbol"] for item in usable],
        "symbols_total": len(universe),
        "symbols_usable_count": len(usable),
        "results": results,
        "limitations": [
            "Only Binance spot 4h archives are covered.",
            "A symbol marked GAPPED must not be used for strategy runs.",
            "Multi-symbol results are not a portfolio: overlapping positions and "
            "correlation are not modelled here.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "usable": len(usable),
                      "total": len(universe),
                      "symbols": [item["symbol"] for item in usable]}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
