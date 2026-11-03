from __future__ import annotations

import hashlib
import json

import pytest

from emergenzix_api.research_compute_routing import (
    build_compute_plan,
    job_for_node,
    verify_compute_plan,
    verify_local_collection,
)


def _snapshot_set() -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": "emergenzix-research-snapshot-set-v1",
        "mode": "RESEARCH_ONLY",
        "selected_start": "2025-11-01T00:00:00+00:00",
        "selected_end_exclusive": "2026-11-02T00:00:00+00:00",
        "local_mirror_collection_sha256": "0" * 64,
        "members": [],
        "live_order_permission": False,
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    manifest["collection_id"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def test_fixed_plan_routes_classic_to_cpu_and_models_to_gpu() -> None:
    first = build_compute_plan(snapshot_set=_snapshot_set(), code_commit="abc1234")
    second = build_compute_plan(snapshot_set=_snapshot_set(), code_commit="abc1234")

    assert first["plan_id"] == second["plan_id"]
    assert job_for_node(first, job_id="classic-turtle-matrix", node_class="cpu")
    assert job_for_node(first, job_id="chronos-zero-shot", node_class="gpu")
    assert job_for_node(first, job_id="qlib-locked-models", node_class="gpu")
    with pytest.raises(ValueError, match="routed to cpu"):
        job_for_node(first, job_id="classic-turtle-matrix", node_class="gpu")
    with pytest.raises(ValueError, match="routed to gpu"):
        job_for_node(first, job_id="chronos-zero-shot", node_class="cpu")


def test_tampered_plan_and_wrong_local_collection_fail_closed(tmp_path) -> None:
    plan = build_compute_plan(snapshot_set=_snapshot_set(), code_commit="abc1234")
    plan["jobs"][0]["node_class"] = "gpu"
    with pytest.raises(ValueError, match="plan ID is invalid"):
        verify_compute_plan(plan)

    valid = build_compute_plan(snapshot_set=_snapshot_set(), code_commit="abc1234")
    (tmp_path / "btcusdt.parquet").write_bytes(b"not-the-pinned-collection")
    with pytest.raises(ValueError, match="does not match"):
        verify_local_collection(valid, tmp_path)
