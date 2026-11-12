#!/usr/bin/env python3
"""Build and audit BRLA-G2 stage-one labels from real BTCUSDT 15m Parquet."""

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
from emergenzix_api.brla_g2_stage1_dataset import build_stage1_dataset  # noqa: E402
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stride-bars", type=int, default=672,
                        help="Default seven-day stride prevents label-window overlap.")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("output file already exists")
    files = sorted(args.data_dir.glob("btcusdt-*.parquet"))
    if not files:
        raise ValueError("BTCUSDT Parquet files are missing")
    source_hash = hashlib.sha256()
    frames = []
    for path in files:
        source_hash.update(path.read_bytes()); frames.append(pd.read_parquet(path))
    frame = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    if frame["timestamp"].duplicated().any():
        raise ValueError("real BTC source contains duplicate timestamps")
    source = frame[["timestamp", "open", "high", "low", "close", "base_volume",
                    "is_closed", "is_imputed"]].to_dict("records")
    fingerprint = source_hash.hexdigest()
    dataset = build_stage1_dataset(source, scenario_sha256=fingerprint,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE1_TRAIN,
        stride_bars=args.stride_bars)
    report = audit_stage1_datasets([dataset], source_sha256=fingerprint,
        source_start=pd.Timestamp(frame["timestamp"].iloc[0]).isoformat(),
        source_end=pd.Timestamp(frame["timestamp"].iloc[-1]).isoformat())
    report["source_files"] = [path.name for path in files]
    report["limitations"] = [
        "The available real-BTC history is audited as one TRAIN research series.",
        "Independent DEVELOPMENT and SEALED_TEST coverage requires additional non-overlapping history.",
        "This report audits labels and readiness; it does not train or promote a model."]
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
