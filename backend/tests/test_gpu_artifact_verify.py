"""CPU-only tests for the independent E001 artifact verifier."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_gpu_lab.verify import ArtifactVerificationError, verify_run


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _seal(run):
    names = ("manifest.json", "metrics.json", "test_predictions.json",
             "model_weights.pt")
    _write_json(run / "sha256.json", {
        name: hashlib.sha256((run / name).read_bytes()).hexdigest()
        for name in names
    })


def _synthetic_run(tmp_path):
    run = tmp_path / "e001"
    run.mkdir(parents=True)
    first = datetime(2024, 1, 5, tzinfo=timezone.utc)
    actual = [-0.02, 0.01, 0.005, -0.003, 0.001]
    forecast = [-0.01, 0.002, 0.004, 0.001, -0.002]
    rows = []
    for index, (a, p) in enumerate(zip(actual, forecast)):
        anchor = first + timedelta(hours=index)
        rows.append({
            "anchor_at": anchor.isoformat(),
            "target_at": (anchor + timedelta(hours=2)).isoformat(),
            "actual_log_return": a,
            "predicted_log_return": p,
        })

    def metrics(pred):
        return {
            "mse": sum((a - p) ** 2 for a, p in zip(actual, pred)) / len(actual),
            "mae": sum(abs(a - p) for a, p in zip(actual, pred)) / len(actual),
            "direction_accuracy": sum(
                (a > 0) == (p > 0) for a, p in zip(actual, pred)
            ) / len(actual),
        }

    manifest = {
        "protocol": "gpu-alpha-001",
        "research_only": True,
        "no_order_execution": True,
        "dataset_sha256": "a" * 64,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "model": "mlp",
        "seed": 42,
        "lookback": 16,
        "horizon": 2,
        "epochs": 5,
        "train_cut": 70,
        "validation_cut": 85,
        "train_examples": 50,
        "validation_examples": 12,
        "test_examples": len(rows),
        "train_final_label_at": "2024-01-01T23:00:00+00:00",
        "validation_first_feature_at": "2024-01-02T00:00:00+00:00",
        "validation_final_label_at": "2024-01-03T23:00:00+00:00",
        "test_first_feature_at": first.isoformat(),
        "test_final_label_at": rows[-1]["target_at"],
        "feature_names": [
            "log_return", "log_high_low", "log_close_open",
            "log1p_volume_change",
        ],
        "feature_mean_train_only": [0.0] * 4,
        "feature_std_train_only": [1.0] * 4,
    }
    _write_json(run / "manifest.json", manifest)
    _write_json(run / "metrics.json", {
        "test": metrics(forecast),
        "zero_return_baseline": metrics([0.0] * len(actual)),
        "validation_mse_best": 0.001,
        "best_epoch": 3,
    })
    _write_json(run / "test_predictions.json", rows)
    # Deliberately not a valid PyTorch checkpoint: verification must not load it.
    (run / "model_weights.pt").write_bytes(b"do not unpickle this file")
    _seal(run)
    return run


def test_valid_artifacts_verify_without_torch_or_gpu(tmp_path):
    run = _synthetic_run(tmp_path)
    outcome = verify_run(run)
    assert outcome["status"] == "verified_self_consistent"
    assert outcome["test_examples"] == 5
    assert outcome["source_dataset_reverified"] is False
    assert outcome["model_weights_loaded"] is False
    assert outcome["trading_permission"] is False


def test_changed_file_fails_digest_check(tmp_path):
    run = _synthetic_run(tmp_path)
    with (run / "model_weights.pt").open("ab") as output:
        output.write(b"tampering")
    with pytest.raises(ArtifactVerificationError, match="SHA-256 mismatch"):
        verify_run(run)


def test_valid_checksums_do_not_hide_rewritten_metrics(tmp_path):
    run = _synthetic_run(tmp_path)
    path = run / "metrics.json"
    data = json.loads(path.read_text())
    data["test"]["mse"] = 0.25
    _write_json(path, data)
    _seal(run)
    with pytest.raises(ArtifactVerificationError, match="does not match"):
        verify_run(run)


def test_valid_checksums_do_not_hide_out_of_order_predictions(tmp_path):
    run = _synthetic_run(tmp_path)
    path = run / "test_predictions.json"
    rows = json.loads(path.read_text())
    rows[2]["anchor_at"] = rows[1]["anchor_at"]
    rows[2]["target_at"] = rows[1]["target_at"]
    _write_json(path, rows)
    _seal(run)
    with pytest.raises(ArtifactVerificationError, match="unordered anchor"):
        verify_run(run)


def test_manifest_split_overlap_is_rejected_after_rehash(tmp_path):
    run = _synthetic_run(tmp_path)
    path = run / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["validation_final_label_at"] = manifest["test_first_feature_at"]
    _write_json(path, manifest)
    _seal(run)
    with pytest.raises(ArtifactVerificationError, match="boundaries overlap"):
        verify_run(run)


def test_research_safety_flags_fail_closed(tmp_path):
    run = _synthetic_run(tmp_path)
    path = run / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["no_order_execution"] = False
    _write_json(path, manifest)
    _seal(run)
    with pytest.raises(ArtifactVerificationError, match="research-only"):
        verify_run(run)


def test_symlink_artifact_is_rejected(tmp_path):
    run = _synthetic_run(tmp_path)
    original = tmp_path / "weights.bin"
    (run / "model_weights.pt").rename(original)
    (run / "model_weights.pt").symlink_to(original)
    with pytest.raises(ArtifactVerificationError, match="unsafe artifact"):
        verify_run(run)


def test_missing_predictions_and_duplicate_keys_are_rejected(tmp_path):
    run = _synthetic_run(tmp_path)
    path = run / "test_predictions.json"
    path.unlink()
    with pytest.raises(ArtifactVerificationError, match="missing or unsafe"):
        verify_run(run)

    run = _synthetic_run(tmp_path / "other")
    (run / "metrics.json").write_text('{"best_epoch": 1, "best_epoch": 2}')
    _seal(run)
    with pytest.raises(ArtifactVerificationError, match="duplicate JSON key"):
        verify_run(run)
