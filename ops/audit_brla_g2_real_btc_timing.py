#!/usr/bin/env python3
"""Audit BRLA-G2 v2 timing labels on real BTCUSDT 15m history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_dataset_audit import audit_stage1_datasets  # noqa: E402
from emergenzix_api.brla_g2_stage1_dataset import PROTOCOL as BASE_PROTOCOL, Stage1Dataset  # noqa: E402
from emergenzix_api.brla_g2_timing_audit import audit_timing_datasets  # noqa: E402
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brla_g2_trend_timing import build_stage1_timing_dataset  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stride-bars", type=int, default=672)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("output file already exists")
    files = sorted(args.data_dir.glob("btcusdt-*.parquet"))
    if not files:
        raise ValueError("BTCUSDT Parquet files are missing")
    source_hash = hashlib.sha256(); frames = []
    for path in files:
        source_hash.update(path.read_bytes()); frames.append(pd.read_parquet(path))
    frame = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    source = frame[["timestamp", "open", "high", "low", "close", "base_volume",
                    "is_closed", "is_imputed"]].to_dict("records")
    fingerprint = source_hash.hexdigest()
    timing = build_stage1_timing_dataset(source, scenario_sha256=fingerprint,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE1_TRAIN,
        stride_bars=args.stride_bars)
    base_examples = tuple(example.base_example for example in timing.examples)
    base = Stage1Dataset(BASE_PROTOCOL, fingerprint, Partition.TRAIN,
        DataPurpose.STAGE1_TRAIN, args.stride_bars, base_examples,
        tuple(example.example_sha256 for example in base_examples),
        timing.base_dataset_sha256)
    base_audit = audit_stage1_datasets([base], source_sha256=fingerprint,
        source_start=pd.Timestamp(frame["timestamp"].iloc[0]).isoformat(),
        source_end=pd.Timestamp(frame["timestamp"].iloc[-1]).isoformat())
    report = audit_timing_datasets([timing], base_audit=base_audit,
                                   source_sha256=fingerprint)
    report["base_audit"] = base_audit
    report["source_files"] = [path.name for path in files]
    report["limitations"] = ["Only one real-BTC TRAIN series is available.",
        "DEVELOPMENT and SEALED_TEST remain absent.",
        "This audit does not authorize encoder training or any order."]
    report["report_sha256"] = hashlib.sha256(json.dumps({
        key: value for key, value in report.items() if key != "report_sha256"},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"output": str(args.output), "decision": report["decision"],
        "examples": report["example_count"], "report_sha256": report["report_sha256"]}))


if __name__ == "__main__":
    main()
