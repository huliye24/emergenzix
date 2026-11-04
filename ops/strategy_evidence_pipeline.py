#!/usr/bin/env python3
"""Run one stage of the CPU -> GPU -> CPU strategy-evidence pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from emergenzix_api.chronos_signal_export import create_chronos_record
from emergenzix_api.market_data import fetch_public_ohlcv
from emergenzix_api.strategy_evidence_pipeline import (
    build_model_prediction,
    build_strategy_analysis,
    merge_node_results,
    write_json_atomic,
)


def _revision(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _completed_market(now: datetime) -> dict:
    payload = fetch_public_ohlcv(
        exchange_id="binance", symbol="BTCUSDT", timeframe="4h", limit=200
    )
    completed = []
    for bar in payload["bars"]:
        opened_at = datetime.fromisoformat(str(bar["timestamp"]).replace("Z", "+00:00"))
        if opened_at + timedelta(hours=4) <= now:
            completed.append(bar)
    if len(completed) < 56:
        raise ValueError("fewer than 56 completed BTCUSDT 4h bars")
    return {"exchange": "binance", "symbol": "BTCUSDT", "timeframe": "4h", "bars": completed}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("cpu", "gpu", "merge"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--chronos-cache", type=Path)
    parser.add_argument("--model-id", default="amazon/chronos-bolt-small")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    revision = _revision(args.repo_root)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    if args.stage == "cpu":
        market = _completed_market(now)
        strategy = build_strategy_analysis(market, generated_at=now, source_revision=revision)
        write_json_atomic(args.work_dir / "market-input.json", market)
        write_json_atomic(args.work_dir / "strategy-analysis.json", strategy)
        result = {"stage": "cpu", "completed_at": strategy["market"]["completed_at"],
                  "record_sha256": strategy["record_sha256"], "order_permission": False}
    elif args.stage == "gpu":
        if args.chronos_cache is None:
            raise ValueError("--chronos-cache is required for the GPU stage")
        market = _load(args.work_dir / "market-input.json")
        strategy = _load(args.work_dir / "strategy-analysis.json")
        signal = create_chronos_record(market, model_id=args.model_id, cache=args.chronos_cache,
                                       generated_at=now)
        prediction = build_model_prediction({"signals": [signal]},
            expected_market=strategy["market"], generated_at=now, source_revision=revision)
        write_json_atomic(args.work_dir / "model-prediction.json", prediction)
        result = {"stage": "gpu", "completed_at": prediction["market"]["completed_at"],
                  "record_sha256": prediction["record_sha256"], "order_permission": False}
    else:
        strategy = _load(args.work_dir / "strategy-analysis.json")
        prediction = _load(args.work_dir / "model-prediction.json")
        evidence = merge_node_results(strategy, prediction, now=now)
        immutable = args.work_dir / "evidence" / f"{evidence['record_sha256']}.json"
        write_json_atomic(immutable, evidence)
        write_json_atomic(args.work_dir / "evidence" / "latest.json", evidence)
        result = {"stage": "merge", "completed_at": evidence["market"]["completed_at"],
                  "record_sha256": evidence["record_sha256"],
                  "output": str(immutable), "order_permission": False}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
