#!/usr/bin/env python3
"""Train BRLA-G2 stage-one encoder without opening SEALED_TEST."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_order_encoder import train_encoder, train_encoder_directory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--stride-bars", type=int, default=96)
    args = parser.parse_args()
    if args.dataset.is_dir():
        report = train_encoder_directory(args.dataset, output=args.checkpoint,
            epochs=args.epochs, stride_bars=args.stride_bars)
    else:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        report = train_encoder(dataset, output=args.checkpoint, epochs=args.epochs,
                               stride_bars=args.stride_bars)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "history"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
