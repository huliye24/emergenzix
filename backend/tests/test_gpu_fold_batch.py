"""CPU-only batch orchestration tests; most do not require PyTorch."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.schemas import DatasetCreate
from emergenzix_gpu_lab import fold_batch


def _registered(tmp_path):
    path = tmp_path / "market.db"
    db = Database(str(path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(1030):
        close = 100 + i * 0.02 + 0.8 * math.sin(i / 13)
        bars.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": float(1000 + (i % 11)),
        })
    row, inserted = persist_dataset(
        db, DatasetCreate(
            symbol="BTC/USDT", timeframe="1h",
            source="synthetic_demo", bars=bars,
        ),
    )
    assert inserted
    return path, row["id"]


def _fake_executor(monkeypatch, *, reject_fold=None, fail_training_fold=None,
                   mismatch_fold=None):
    """Exercise orchestration without paying for model training/GPU."""
    calls = []

    def train(**kwargs):
        n = kwargs["fold_number"]
        calls.append(("train", n))
        folder = Path(kwargs["output_dir"])
        folder.mkdir()
        (folder / "sha256.json").write_text(
            json.dumps({"fake_test_marker": n}) + "\n",
            encoding="utf-8",
        )
        if n == fail_training_fold:
            raise RuntimeError("simulated training interruption")
        plan = json.loads((folder.parent / "batch_plan.json").read_text())["plan"]
        return {
            "output_dir": str(folder),
            "fold_number": n,
            "plan_sha256": plan["plan_sha256"],
            "device": kwargs["device_name"],
        }

    def verify(run_dir, *, db_path):
        folder = Path(run_dir)
        n = int(folder.name.split("-")[1])
        calls.append(("verify", n))
        if n == reject_fold:
            raise ValueError("simulated tampered fold evidence")
        batch = json.loads((folder.parent / "batch_plan.json").read_text())
        return {
            "status": "verified_against_source",
            "fold_number": n,
            "dataset_sha256": batch["dataset_sha256"],
            "plan_sha256": batch["plan"]["plan_sha256"],
            "model": batch["training"]["model"],
            "source_dataset_reverified": True,
            "source_plan_reconstructed": True,
            "model_weights_loaded": False,
            "trading_permission": (n == mismatch_fold),
            "test_examples": batch["plan"]["windows"][n - 1]["test"]["sample_count"],
        }

    monkeypatch.setattr(fold_batch, "train_one_fold", train)
    monkeypatch.setattr(fold_batch, "verify_fold_run", verify)
    return calls


def test_batch_runs_all_folds_once_in_order_and_seals_last(tmp_path, monkeypatch):
    path, dataset_id = _registered(tmp_path)
    data_sha_before = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = _fake_executor(monkeypatch)
    destination = tmp_path / "batch-new"
    result = fold_batch.run_walk_forward_batch(
        db_path=str(path), dataset_id=dataset_id,
        output_dir=str(destination), device_name="cpu", epochs=1,
    )
    assert calls == [
        ("train", 1), ("verify", 1),
        ("train", 2), ("verify", 2),
        ("train", 3), ("verify", 3),
    ]
    assert result["status"] == "completed_verified_folds"
    assert result["folds_verified"] == 3
    assert result["trading_permission"] is False
    recorded_plan = json.loads((destination / "batch_plan.json").read_text())
    completed = json.loads((destination / "batch_complete.json").read_text())
    assert completed["batch_plan_file_sha256"] == hashlib.sha256(
        (destination / "batch_plan.json").read_bytes()
    ).hexdigest()
    assert completed["dataset_sha256"] == dataset_id
    assert completed["plan_sha256"] == recorded_plan["plan"]["plan_sha256"]
    assert completed["folds_planned"] == completed["folds_verified"] == 3
    assert [fold["fold_number"] for fold in completed["fold_results"]] == [1, 2, 3]
    for fold in completed["fold_results"]:
        directory = destination / fold["relative_directory"]
        assert fold["fold_sha256_json_sha256"] == hashlib.sha256(
            (directory / "sha256.json").read_bytes()
        ).hexdigest()
    assert not (destination / ".batch-complete.tmp").exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == data_sha_before
    assert sorted(p.name for p in destination.iterdir()) == [
        "batch_complete.json", "batch_plan.json",
        "fold-001", "fold-002", "fold-003",
    ]
    with pytest.raises(FileExistsError, match="already exists"):
        fold_batch.run_walk_forward_batch(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(destination), device_name="cpu", epochs=1,
        )
    assert len(calls) == 6


@pytest.mark.parametrize("failure", ["train", "verify", "unsafe_evidence"])
def test_mid_batch_failure_never_publishes_success_or_attempts_later_folds(
    tmp_path, monkeypatch, failure,
):
    path, dataset_id = _registered(tmp_path)
    opts = {
        "fail_training_fold": 2 if failure == "train" else None,
        "reject_fold": 2 if failure == "verify" else None,
        "mismatch_fold": 2 if failure == "unsafe_evidence" else None,
    }
    calls = _fake_executor(monkeypatch, **opts)
    directory = tmp_path / f"failed-{failure}"
    expected = RuntimeError if failure == "train" else ValueError
    with pytest.raises(expected):
        fold_batch.run_walk_forward_batch(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(directory), device_name="cpu", epochs=1,
        )
    assert (directory / "batch_plan.json").is_file()
    assert (directory / "fold-001" / "sha256.json").is_file()
    assert not (directory / "fold-003").exists()
    assert not (directory / "batch_complete.json").exists()
    assert not any(n == 3 for _, n in calls)
    with pytest.raises(FileExistsError):
        fold_batch.run_walk_forward_batch(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(directory), device_name="cpu", epochs=1,
        )


@pytest.mark.parametrize("overrides,pattern", [
    ({"folds": 25}, "folds"),
    ({"folds": 1}, "folds"),
    ({"train_bars": 40}, "train_bars"),
    ({"test_bars": 128, "step_bars": 1}, "overlap"),
    ({"epochs": 0}, "invalid fold"),
    ({"seed": True}, "invalid fold"),
    ({"device_name": "auto"}, "explicit cpu/cuda"),
    ({"learning_rate": float("nan")}, "invalid fold"),
])
def test_invalid_batch_rejected_before_directory_or_training(
    tmp_path, monkeypatch, overrides, pattern,
):
    path, dataset_id = _registered(tmp_path)
    calls = _fake_executor(monkeypatch)
    directory = tmp_path / "never-created"
    args = dict(
        db_path=str(path), dataset_id=dataset_id,
        output_dir=str(directory), device_name="cpu",
    )
    args.update(overrides)
    with pytest.raises(ValueError, match=pattern):
        fold_batch.run_walk_forward_batch(**args)
    assert not directory.exists()
    assert calls == []


def test_short_dataset_refuses_partial_batch(tmp_path, monkeypatch):
    path, dataset_id = _registered(tmp_path)
    calls = _fake_executor(monkeypatch)
    directory = tmp_path / "missing-history"
    with pytest.raises(ValueError, match="insufficient history"):
        fold_batch.run_walk_forward_batch(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(directory), folds=8, device_name="cpu",
        )
    assert calls == []
    assert not directory.exists()


def test_symlink_batch_root_is_rejected(tmp_path, monkeypatch):
    path, dataset_id = _registered(tmp_path)
    calls = _fake_executor(monkeypatch)
    actual = tmp_path / "existing-dir"
    actual.mkdir()
    link = tmp_path / "symlink"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(FileExistsError, match="already exists"):
        fold_batch.run_walk_forward_batch(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(link), device_name="cpu",
        )
    assert calls == []


def test_cpu_two_fold_real_training_integration_optional(tmp_path):
    pytest.importorskip("torch")
    path, dataset_id = _registered(tmp_path)
    target = tmp_path / "real-folds"
    result = fold_batch.run_walk_forward_batch(
        db_path=str(path), dataset_id=dataset_id,
        output_dir=str(target), folds=2, device_name="cpu",
        model_name="mlp", epochs=1, batch_size=256, seed=11,
    )
    assert result["folds_verified"] == 2
    assert (target / "batch_complete.json").exists()
    complete = json.loads((target / "batch_complete.json").read_text())
    assert complete["folds_verified"] == 2
    assert complete["fold_results"][0]["verification_status"] == "verified_against_source"
