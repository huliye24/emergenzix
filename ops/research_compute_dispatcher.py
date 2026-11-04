#!/usr/bin/env python3
"""Run one allowlisted research job on its pinned CPU or GPU node class."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from emergenzix_api.research_compute_routing import (  # noqa: E402
    job_for_node,
    verify_local_collection,
)
from emergenzix_api.database import Database  # noqa: E402
from emergenzix_api.eami_training_registration import (  # noqa: E402
    register_qlib_training_report,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(
    *,
    plan: dict[str, Any],
    job: dict[str, Any],
    python: Path,
    repo_root: Path,
    data_root: Path,
    run_root: Path,
    model_cache: Path,
) -> tuple[list[str], Path, Path]:
    plan_root = run_root / plan["plan_id"]
    config = job["config"]
    if job["job_id"] == "classic-turtle-matrix":
        output = plan_root / "classic" / "matrix.json"
        command = [
            str(python),
            str(repo_root / "ops" / "turtle_walkforward_matrix.py"),
            "--input-dir", str(data_root),
            "--start", plan["selected_start"],
            "--end", plan["selected_end_exclusive"],
            "--train-days", str(config["train_days"]),
            "--test-days", str(config["test_days"]),
            "--step-days", str(config["step_days"]),
            "--initial-cash", str(config["initial_cash"]),
            "--fee-bps", str(config["fee_bps"]),
            "--slippage-bps", str(config["slippage_bps"]),
            "--output-json", str(output),
            "--output-csv", str(output.with_suffix(".csv")),
        ]
        return command, repo_root, output
    if job["job_id"] == "chronos-zero-shot":
        output = plan_root / "gpu" / "chronos.json"
        command = [
            str(python), "-m", "emergenzix_api.chronos_walk_forward",
            "--data-root", str(data_root),
            "--cache", str(model_cache),
            "--output", str(output),
            "--context-length", str(config["context_length"]),
            "--origins", str(config["origins"]),
            "--round-trip-cost-bps", str(config["round_trip_cost_bps"]),
        ]
        return command, repo_root / "backend", output
    if job["job_id"] == "qlib-locked-models":
        chronos = plan_root / "gpu" / "chronos.json"
        if not chronos.is_file():
            raise ValueError("Qlib dependency is missing: Chronos report not found")
        output = plan_root / "gpu" / "chronos-qlib.json"
        command = [
            str(python), "-m", "emergenzix_api.qlib_gpu_train",
            "--data-root", str(data_root),
            "--chronos-report", str(chronos),
            "--output", str(output),
            "--artifacts", str(plan_root / "gpu" / "qlib-artifacts"),
            "--models", *config["models"],
        ]
        return command, repo_root / "backend", output
    raise ValueError("job runner is not allowlisted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--node-class", choices=("cpu", "gpu"), required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("/dev/null"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--registry-db", type=Path)
    parser.add_argument("--input-asset-id")
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    job = dict(job_for_node(plan, job_id=args.job, node_class=args.node_class))
    dataset_hash = verify_local_collection(plan, args.data_root)
    command, cwd, output = _command(
        plan=plan,
        job=job,
        python=args.python,
        repo_root=args.repo_root,
        data_root=args.data_root,
        run_root=args.run_root,
        model_cache=args.model_cache,
    )
    receipt = output.parent / f"{job['job_id']}.receipt.json"
    if receipt.is_file():
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if (
            saved.get("plan_id") == plan["plan_id"]
            and output.is_file()
            and saved.get("output_sha256") == _sha256(output)
        ):
            print(json.dumps({"status": "already_complete", **saved}, sort_keys=True))
            return 0
        raise ValueError("existing job receipt or output failed integrity checks")
    preview = {
        "status": "planned" if not args.execute else "starting",
        "mode": "RESEARCH_ONLY",
        "plan_id": plan["plan_id"],
        "job_id": job["job_id"],
        "node_class": args.node_class,
        "dataset_collection_sha256": dataset_hash,
        "command": command,
    }
    if not args.execute:
        print(json.dumps(preview, sort_keys=True))
        return 0

    if job["job_id"] == "qlib-locked-models" and (
        args.registry_db is None or args.input_asset_id is None
    ):
        raise ValueError("Qlib execution requires --registry-db and --input-asset-id")

    output.parent.mkdir(parents=True, exist_ok=True)
    if args.model_cache != Path("/dev/null"):
        args.model_cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, cwd=cwd, check=True)
    if not output.is_file():
        raise ValueError("research runner completed without its declared output")
    registration = None
    if job["job_id"] == "qlib-locked-models":
        trainer = args.repo_root / "backend" / "emergenzix_api" / "qlib_gpu_train.py"
        registration = register_qlib_training_report(
            db=Database(str(args.registry_db)),
            input_asset_id=args.input_asset_id,
            report_path=output,
            code_sha256=_sha256(trainer),
        )
    saved = {
        "mode": "RESEARCH_ONLY",
        "plan_id": plan["plan_id"],
        "job_id": job["job_id"],
        "node_class": args.node_class,
        "code_commit": plan["code_commit"],
        "dataset_collection_sha256": dataset_hash,
        "output": str(output),
        "output_sha256": _sha256(output),
        "orders_created": False,
        "live_order_permission": False,
        "eami_registration": (
            None
            if registration is None
            else {
                "input_asset_id": registration["input_asset_id"],
                "output_asset_id": registration["output_asset_id"],
                "model_ids": [item["model"]["id"] for item in registration["models"]],
                "experiment_ids": [
                    item["experiment"]["id"] for item in registration["models"]
                ],
                "automatic_promotion": False,
                "order_permission": False,
            }
        ),
    }
    receipt.write_text(json.dumps(saved, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", **saved}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
