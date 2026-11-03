#!/usr/bin/env python3
"""Run the product Turtle baseline against one immutable OHLCV JSON file."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from emergenzix_api.backtesting import run_turtle_breakout  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _bar(item: dict[str, object]) -> SimpleNamespace:
    timestamp = item["timestamp"]
    if isinstance(timestamp, (int, float)):
        parsed = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return SimpleNamespace(
        timestamp=parsed,
        open=float(item["open"]),
        high=float(item["high"]),
        low=float(item["low"]),
        close=float(item["close"]),
        volume=float(item["volume"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", choices=("1h", "4h", "1d"), required=True)
    parser.add_argument("--initial-cash", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) < 22:
        raise ValueError("input must contain at least 22 OHLCV bars")
    bars = [_bar(item) for item in raw]
    result = run_turtle_breakout(
        bars,
        entry_window=20,
        exit_window=10,
        initial_cash=args.initial_cash,
        fee_bps=10,
        slippage_bps=5,
    )
    report = {
        "protocol": "emergenzix-turtle-20-10-v1",
        "mode": "BACKTEST_ONLY",
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "dataset_sha256": hashlib.sha256(_canonical(raw)).hexdigest(),
        "bars_count": len(raw),
        "start_at": bars[0].timestamp.isoformat(),
        "end_at": bars[-1].timestamp.isoformat(),
        "result": result,
    }
    report["report_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    args.output.write_bytes(_canonical(report) + b"\n")
    print(json.dumps({
        "dataset_sha256": report["dataset_sha256"],
        "report_sha256": report["report_sha256"],
        "return_pct": result["total_return_pct"],
        "max_drawdown_pct": result["max_drawdown_pct"],
        "trades": result["number_of_trades"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
