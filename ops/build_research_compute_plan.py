#!/usr/bin/env python3
"""Build one immutable CPU/GPU compute plan from a snapshot-set manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from emergenzix_api.research_compute_routing import build_compute_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-set", type=Path, required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshot_set = json.loads(args.snapshot_set.read_text(encoding="utf-8"))
    plan = build_compute_plan(
        snapshot_set=snapshot_set,
        code_commit=args.code_commit,
    )
    payload = json.dumps(plan, sort_keys=True, indent=2) + "\n"
    if args.output.exists() and args.output.read_text(encoding="utf-8") != payload:
        raise ValueError("refusing to overwrite a different compute plan")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(
        json.dumps(
            {
                "mode": "RESEARCH_ONLY",
                "plan_id": plan["plan_id"],
                "snapshot_set_id": plan["snapshot_set_id"],
                "routes": {
                    job["job_id"]: job["node_class"] for job in plan["jobs"]
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
