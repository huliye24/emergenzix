#!/usr/bin/env python3
"""Build and audit the first frozen BRMG-G2 virtual pretraining dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from emergenzix_api.brmg_g2_virtual_dataset import (
    audit_virtual_dataset, build_virtual_dataset, write_virtual_dataset_directory,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bars-per-path", type=int, default=1440)
    parser.add_argument("--streaming", action="store_true")
    args = parser.parse_args()
    if args.streaming:
        manifest = write_virtual_dataset_directory(args.output,
            bars_per_path=args.bars_per_path)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    dataset = build_virtual_dataset(bars_per_path=args.bars_per_path)
    audit = audit_virtual_dataset(dataset)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False,
        separators=(",", ":")), encoding="utf-8")
    (args.output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False,
        indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["decision"] == "READY_FOR_STAGE1_VIRTUAL_PRETRAINING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
