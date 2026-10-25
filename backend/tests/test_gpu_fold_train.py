"""Optional CPU acceptance tests for the research-only one-fold GPU executor."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.schemas import DatasetCreate
from emergenzix_gpu_lab.fold_train import train_one_fold


def _registered_data(tmp_path, *, modify_test: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "research.db"
    db = Database(str(path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(1040):
        close = 100.0 + 0.025 * i + 1.5 * math.sin(i / 9)
        # Perturb test-period outcomes, never earlier training/validation data.
        if modify_test and i >= 640:
            close *= 1.04 + 0.015 * math.sin((i - 640) / 5)
        bars.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": float(1000 + i % 31),
        })
    row, inserted = persist_dataset(
        db, DatasetCreate(
            symbol="BTC/USDT", timeframe="1h",
            source="synthetic_demo", bars=bars,
        ),
    )
    assert inserted
    return path, row["id"]


@pytest.mark.parametrize("model_name", ["mlp", "tcn"])
def test_cpu_fold_training_seals_provenance_and_holdout_metrics(
    tmp_path, model_name
):
    pytest.importorskip("torch")
    path, dataset_id = _registered_data(tmp_path)
    original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path / f"fold-1-{model_name}"
    result = train_one_fold(
        db_path=str(path), dataset_id=dataset_id, output_dir=str(output),
        fold_number=1, device_name="cpu", model_name=model_name,
        seed=19, epochs=2, batch_size=256, lookback=32, horizon=2,
    )
    manifest = json.loads((output / "manifest.json").read_text())
    metrics = json.loads((output / "metrics.json").read_text())
    predictions = json.loads((output / "test_predictions.json").read_text())
    digests = json.loads((output / "sha256.json").read_text())
    assert result["device"] == manifest["device"] == "cpu"
    assert manifest["protocol"] == "gpu-walk-forward-fold-v1"
    assert manifest["plan_protocol"] == "gpu-walk-forward-plan-v1"
    assert manifest["fold_number"] == 1
    assert manifest["dataset_sha256"] == dataset_id
    assert result["plan_sha256"] == manifest["plan_sha256"]
    assert manifest["checkpoint_selection"] == "validation_mse_only"
    assert manifest["holdout_evaluations"] == 1
    assert manifest["automatic_strategy_promotion"] is False
    assert manifest["no_order_execution"] is True
    assert manifest["research_only"] is True
    assert 1 <= metrics["best_epoch"] <= 2
    assert metrics["validation_mse_best"] >= 0
    assert len(predictions) == manifest["test_examples"] == 126
    assert predictions[0]["anchor_at"] == manifest["window"]["test"]["first_anchor_at"]
    assert predictions[-1]["target_at"] == manifest["window"]["test"]["last_target_at"]
    assert set(digests) == {
        "model_weights.pt", "manifest.json", "metrics.json",
        "test_predictions.json",
    }
    for filename, digest in digests.items():
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == digest
    # Recompute metrics using written predictions rather than trusting trainer.
    errors = [r["actual_log_return"] - r["predicted_log_return"]
              for r in predictions]
    assert metrics["test"]["mse"] == pytest.approx(
        sum(e * e for e in errors) / len(errors), rel=1e-5
    )
    assert metrics["zero_return_baseline"]["mse"] == pytest.approx(
        sum(r["actual_log_return"] ** 2 for r in predictions) / len(predictions),
        rel=1e-5,
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_digest
    with pytest.raises(FileExistsError):
        train_one_fold(
            db_path=str(path), dataset_id=dataset_id, output_dir=str(output),
            fold_number=1, device_name="cpu", epochs=1,
        )


def test_holdout_mutation_never_changes_epoch_or_checkpoint(tmp_path):
    torch = pytest.importorskip("torch")
    regular_path, regular_id = _registered_data(tmp_path / "normal")
    altered_path, altered_id = _registered_data(
        tmp_path / "changed", modify_test=True,
    )
    assert regular_id != altered_id
    config = dict(
        fold_number=1, model_name="mlp", device_name="cpu",
        seed=17, epochs=2, batch_size=256,
    )
    first_dir, changed_dir = tmp_path / "run-a", tmp_path / "run-b"
    first = train_one_fold(
        db_path=str(regular_path), dataset_id=regular_id,
        output_dir=str(first_dir), **config,
    )
    changed = train_one_fold(
        db_path=str(altered_path), dataset_id=altered_id,
        output_dir=str(changed_dir), **config,
    )
    # Different holdouts must not affect the validation-selected checkpoint.
    assert first["metrics"]["best_epoch"] == changed["metrics"]["best_epoch"]
    assert first["metrics"]["validation_mse_best"] == pytest.approx(
        changed["metrics"]["validation_mse_best"], rel=0, abs=0,
    )
    state_first = torch.load(
        first_dir / "model_weights.pt", map_location="cpu", weights_only=True
    )
    state_changed = torch.load(
        changed_dir / "model_weights.pt", map_location="cpu", weights_only=True
    )
    assert state_first.keys() == state_changed.keys()
    for key in state_first:
        torch.testing.assert_close(
            state_first[key], state_changed[key], rtol=0, atol=0
        )
    assert first["metrics"]["test"]["mse"] != changed["metrics"]["test"]["mse"]


@pytest.mark.parametrize("overrides,match", [
    ({"fold_number": 0}, "invalid fold"),
    ({"fold_number": True}, "invalid fold"),
    ({"seed": -1}, "invalid fold"),
    ({"epochs": 0}, "invalid fold"),
    ({"epochs": 501}, "invalid fold"),
    ({"learning_rate": float("nan")}, "invalid fold"),
    ({"learning_rate": 0}, "invalid fold"),
    ({"batch_size": 0}, "invalid fold"),
    ({"device_name": "auto"}, "explicit cpu/cuda"),
    ({"model_name": "unknown"}, "mlp/tcn"),
])
def test_invalid_training_config_rejected_before_touching_disk(
    tmp_path, overrides, match
):
    target = tmp_path / "not-created"
    params = dict(
        db_path=str(tmp_path / "missing.db"),
        dataset_id="a" * 64,
        output_dir=str(target),
    )
    params.update(overrides)
    with pytest.raises(ValueError, match=match):
        train_one_fold(**params)
    assert not target.exists()
    assert not (tmp_path / "missing.db").exists()


def test_fold_outside_plan_rejected_without_creating_artifacts(tmp_path):
    path, dataset_id = _registered_data(tmp_path)
    output = tmp_path / "out-of-range"
    with pytest.raises(ValueError, match="fold_number exceeds"):
        train_one_fold(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(output), fold_number=4,
            device_name="cpu",
        )
    assert not output.exists()


def test_cuda_request_fails_closed_if_gpu_unavailable(tmp_path):
    torch = pytest.importorskip("torch")
    if torch.cuda.is_available():
        pytest.skip("CPU-only fallback check; CUDA is available")
    path, dataset_id = _registered_data(tmp_path)
    output = tmp_path / "no-cuda-fallback"
    with pytest.raises(RuntimeError, match="CUDA explicitly requested"):
        train_one_fold(
            db_path=str(path), dataset_id=dataset_id,
            output_dir=str(output), fold_number=1,
            device_name="cuda", epochs=1,
        )
    assert not output.exists()
