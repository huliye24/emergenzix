"""Deterministic CPU/GPU routing for immutable research-only experiments."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "emergenzix-research-compute-plan-v1"
JOB_ROUTES = {
    "classic-turtle-matrix": "cpu",
    "chronos-zero-shot": "gpu",
    "qlib-locked-models": "gpu",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def verify_snapshot_set(snapshot_set: Mapping[str, Any]) -> None:
    collection_id = str(snapshot_set.get("collection_id", ""))
    canonical = _canonical(
        {key: value for key, value in snapshot_set.items() if key != "collection_id"}
    )
    if hashlib.sha256(canonical).hexdigest() != collection_id:
        raise ValueError("snapshot-set collection ID is invalid")
    if snapshot_set.get("mode") != "RESEARCH_ONLY":
        raise ValueError("snapshot set must be RESEARCH_ONLY")
    if snapshot_set.get("live_order_permission") is not False:
        raise ValueError("snapshot set must explicitly deny LIVE permission")


def build_compute_plan(
    *, snapshot_set: Mapping[str, Any], code_commit: str
) -> dict[str, Any]:
    """Build the fixed classic/Chronos/Qlib job graph for one snapshot set."""
    verify_snapshot_set(snapshot_set)
    if len(code_commit) < 7 or any(
        character not in "0123456789abcdef" for character in code_commit.lower()
    ):
        raise ValueError("code commit must be a hexadecimal Git revision")
    jobs = [
        {
            "job_id": "classic-turtle-matrix",
            "node_class": "cpu",
            "runner": "turtle_walkforward_matrix-v1",
            "depends_on": [],
            "config": {
                "train_days": 120,
                "test_days": 60,
                "step_days": 60,
                "initial_cash": 10_000.0,
                "fee_bps": 10.0,
                "slippage_bps": 5.0,
            },
        },
        {
            "job_id": "chronos-zero-shot",
            "node_class": "gpu",
            "runner": "chronos-zero-shot-walk-forward-v1",
            "depends_on": [],
            "config": {
                "context_length": 512,
                "origins": 8,
                "round_trip_cost_bps": 30.0,
            },
        },
        {
            "job_id": "qlib-locked-models",
            "node_class": "gpu",
            "runner": "chronos-qlib-locked-comparison-v1",
            "depends_on": ["chronos-zero-shot"],
            "config": {
                "models": ["transformer", "lstm", "alstm", "tra"],
                "seed": 20261103,
            },
        },
    ]
    plan = {
        "schema_version": SCHEMA_VERSION,
        "mode": "RESEARCH_ONLY",
        "snapshot_set_id": snapshot_set["collection_id"],
        "snapshot_set_manifest_key": (
            "market-data/v2/collections/"
            f"snapshot={snapshot_set['collection_id']}/manifest.json"
        ),
        "dataset_collection_sha256": snapshot_set[
            "local_mirror_collection_sha256"
        ],
        "selected_start": snapshot_set["selected_start"],
        "selected_end_exclusive": snapshot_set["selected_end_exclusive"],
        "code_commit": code_commit.lower(),
        "jobs": jobs,
        "orders_created": False,
        "automatic_strategy_promotion": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
    }
    plan_id = hashlib.sha256(_canonical(plan)).hexdigest()
    return {**plan, "plan_id": plan_id}


def verify_compute_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported compute-plan schema")
    plan_id = str(plan.get("plan_id", ""))
    canonical = _canonical({key: value for key, value in plan.items() if key != "plan_id"})
    if hashlib.sha256(canonical).hexdigest() != plan_id:
        raise ValueError("compute plan ID is invalid")
    if plan.get("mode") != "RESEARCH_ONLY":
        raise ValueError("compute plan must be RESEARCH_ONLY")
    for permission in (
        "orders_created",
        "automatic_strategy_promotion",
        "paper_order_permission",
        "testnet_order_permission",
        "live_order_permission",
    ):
        if plan.get(permission) is not False:
            raise ValueError(f"compute plan must deny {permission}")
    jobs = list(plan.get("jobs", []))
    if [job.get("job_id") for job in jobs] != list(JOB_ROUTES):
        raise ValueError("compute plan job graph is not the fixed v1 graph")
    for job in jobs:
        if job.get("node_class") != JOB_ROUTES[job["job_id"]]:
            raise ValueError(f"invalid route for {job['job_id']}")


def job_for_node(
    plan: Mapping[str, Any], *, job_id: str, node_class: str
) -> Mapping[str, Any]:
    verify_compute_plan(plan)
    if node_class not in {"cpu", "gpu"}:
        raise ValueError("node class must be cpu or gpu")
    try:
        job = next(job for job in plan["jobs"] if job["job_id"] == job_id)
    except StopIteration as exc:
        raise ValueError(f"unknown research job: {job_id}") from exc
    if job["node_class"] != node_class:
        raise ValueError(f"{job_id} is routed to {job['node_class']}, not {node_class}")
    return job


def collection_sha256(root: Path) -> str:
    files = sorted(root.glob("*.parquet"), key=lambda path: path.name)
    if not files:
        raise ValueError("no Parquet inputs found")
    digest = hashlib.sha256()
    for path in files:
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{file_hash}  {path.name}\n".encode())
    return digest.hexdigest()


def verify_local_collection(plan: Mapping[str, Any], root: Path) -> str:
    verify_compute_plan(plan)
    observed = collection_sha256(root)
    if observed != plan["dataset_collection_sha256"]:
        raise ValueError("local Parquet collection does not match the pinned plan")
    return observed
