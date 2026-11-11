#!/usr/bin/env python3
"""Generate unseen-seed BRMG-G2 PAPER exam paths."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brmg_g2_virtual_dataset import write_independent_exam_directory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bars-per-path", type=int, default=25920)
    args = parser.parse_args()
    manifest = write_independent_exam_directory(args.output,
                                                 bars_per_path=args.bars_per_path)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
