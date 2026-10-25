"""CPU-only, no-checkpoint-loading tests for fold artifact verification."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.schemas import DatasetCreate
from emergenzix_gpu_lab.dataset import (
    causal_ohlcv_features, load_registered_dataset,
)
from emergenzix_gpu_lab.fold_verify import FoldVerificationError, verify_fold_run
from emergenzix_gpu_lab.walk_forward import describe_plan


_ARTIFACTS = (
    "model_weights.pt", "manifest.json", "metrics.json", "test_predictions.json",
)


def _json(path, value):
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _registered(tmp_path, *, price_shift=0.0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data.db"
    database = Database(str(db_path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(1030):
        close = 100 + 0.02 * i + 1.6 * math.sin(i / 9)
        if i >= 640:
            close *= 1 + price_shift * math.sin(i / 7)
        bars.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": float(1000 + i % 13),
        })
    row, inserted = persist_dataset(
        database, DatasetCreate(
            symbol="BTC/USDT", timeframe="1h", source="synthetic_demo",
            bars=bars,
        )
    )
    assert inserted
    return db_path, row["id"]


def _scores(y, p):
    return {
        "mse": float(np.mean((y - p) ** 2)),
        "mae": float(np.mean(np.abs(y - p))),
        "direction_accuracy": float(np.mean((y > 0) == (p > 0))),
    }


def _reseal(run):
    _json(run / "sha256.json", {
        name: hashlib.sha256((run / name).read_bytes()).hexdigest()
        for name in _ARTIFACTS
    })


def _fixture(tmp_path):
    db_path, dataset_id = _registered(tmp_path)
    dataset = load_registered_dataset(db_path, dataset_id)
    plan = describe_plan(dataset)
    window = plan["windows"][0]
    feature_stop = window["train"]["anchors"][1]
    train_features = causal_ohlcv_features(dataset)[:feature_stop]
    mean = train_features.mean(axis=0, dtype=np.float64)
    std = np.maximum(train_features.std(axis=0, dtype=np.float64), 1e-8)
    start, stop = window["test"]["anchors"]
    indexes = np.arange(start, stop, dtype=np.int64)
    actual = np.log(dataset.close[indexes + 1] /
                    dataset.close[indexes]).astype(np.float32)
    predicted = (actual * np.float32(0.65) + np.float32(0.001)).astype(np.float32)
    rows = [
        {
            "anchor_at": dataset.times[int(i)],
            "target_at": dataset.times[int(i) + 1],
            "actual_log_return": float(y),
            "predicted_log_return": float(p),
        }
        for i, y, p in zip(indexes, actual, predicted)
    ]
    run = tmp_path / "fold-01"
    run.mkdir()
    (run / "model_weights.pt").write_bytes(
        b"not a valid torch checkpoint; verifier MUST NOT deserialize this"
    )
    manifest = {
        "protocol": "gpu-walk-forward-fold-v1",
        "plan_protocol": "gpu-walk-forward-plan-v1",
        "plan_sha256": plan["plan_sha256"],
        "dataset_sha256": dataset_id,
        "symbol": dataset.symbol,
        "timeframe": dataset.timeframe,
        "fold_number": 1,
        "folds_planned": 3,
        "plan_configuration": plan["configuration"],
        "window": window,
        "model": "mlp",
        "device": "cpu",
        "seed": 17,
        "epochs": 3,
        "batch_size": 64,
        "learning_rate": 0.001,
        "lookback": 32,
        "horizon": 1,
        "train_examples": window["train"]["sample_count"],
        "validation_examples": window["validation"]["sample_count"],
        "test_examples": window["test"]["sample_count"],
        "feature_names": [
            "log_return", "log_high_low", "log_close_open",
            "log1p_volume_change",
        ],
        "feature_mean_train_only": mean.tolist(),
        "feature_std_train_only": std.tolist(),
        "checkpoint_selection": "validation_mse_only",
        "holdout_evaluations": 1,
        "research_only": True,
        "no_order_execution": True,
        "automatic_strategy_promotion": False,
    }
    _json(run / "manifest.json", manifest)
    _json(run / "metrics.json", {
        "best_epoch": 2,
        "validation_mse_best": 0.001,
        "test": _scores(actual, predicted),
        "zero_return_baseline": _scores(actual, np.zeros_like(actual)),
    })
    _json(run / "test_predictions.json", rows)
    _reseal(run)
    return db_path, run


def _change_and_reseal(run, filename, edit):
    path = run / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    _json(path, data)
    _reseal(run)


def test_accepts_complete_provenanced_fold_without_loading_weights(tmp_path):
    db, run = _fixture(tmp_path)
    db_sha_before = hashlib.sha256(db.read_bytes()).hexdigest()
    files_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in run.iterdir()}
    result = verify_fold_run(run, db_path=db)
    assert result["status"] == "verified_against_source"
    assert result["protocol"] == "gpu-walk-forward-fold-v1"
    assert result["fold_number"] == 1
    assert result["test_examples"] == 127
    assert result["source_dataset_reverified"] is True
    assert result["source_plan_reconstructed"] is True
    assert result["model_weights_loaded"] is False
    assert result["trading_permission"] is False
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_sha_before
    assert files_before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in run.iterdir()
    }


def test_detects_checkpoint_change_without_loading_it(tmp_path):
    db, run = _fixture(tmp_path)
    with (run / "model_weights.pt").open("ab") as output:
        output.write(b"changed")
    with pytest.raises(FoldVerificationError, match="SHA-256 mismatch"):
        verify_fold_run(run, db_path=db)


@pytest.mark.parametrize("filename,edit,reason", [
    ("manifest.json",
     lambda x: x.update(plan_sha256="a" * 64),
     "plan_sha256 disagrees"),
    ("manifest.json",
     lambda x: x["window"]["test"]["anchors"].__setitem__(0, 642),
     "fold boundaries"),
    ("manifest.json",
     lambda x: x["feature_mean_train_only"].__setitem__(0, 100.0),
     "feature_mean_train_only"),
    ("manifest.json",
     lambda x: x.update(automatic_strategy_promotion=True),
     "unsafe or unsupported"),
    ("manifest.json",
     lambda x: x.update(holdout_evaluations=2),
     "unsafe or unsupported"),
    ("manifest.json",
     lambda x: x.update(dataset_sha256="b" * 64),
     "registered dataset does not exist"),
    ("metrics.json",
     lambda x: x["test"].update(mse=0.95),
     "test.mse disagrees"),
    ("test_predictions.json",
     lambda x: x[1].update(anchor_at=x[0]["anchor_at"]),
     "incorrect or reordered"),
    ("test_predictions.json",
     lambda x: x[0].update(actual_log_return=3.0),
     "holdout label disagrees"),
    ("test_predictions.json",
     lambda x: x[0].update(predicted_log_return=float("inf")),
     "Out of range"),  # overridden below with a finite forgery
])
def test_rehash_cannot_hide_forged_source_evidence(
    tmp_path, filename, edit, reason
):
    # JSON doesn't encode infinity; test real finite forgery below instead.
    if reason == "Out of range":
        pytest.skip("nonfinite JSON covered by separate test")
    db, run = _fixture(tmp_path)
    _change_and_reseal(run, filename, edit)
    with pytest.raises(ValueError, match=reason):
        verify_fold_run(run, db_path=db)


def test_even_rehashed_predictions_and_metrics_cannot_forge_actual_prices(tmp_path):
    db, run = _fixture(tmp_path)
    rows = json.loads((run / "test_predictions.json").read_text())
    rows[0]["actual_log_return"] += 0.01
    _json(run / "test_predictions.json", rows)
    actual = np.array([r["actual_log_return"] for r in rows], dtype=np.float32)
    pred = np.array([r["predicted_log_return"] for r in rows], dtype=np.float32)
    metrics = json.loads((run / "metrics.json").read_text())
    metrics["test"] = _scores(actual, pred)
    metrics["zero_return_baseline"] = _scores(actual, np.zeros_like(actual))
    _json(run / "metrics.json", metrics)
    _reseal(run)
    with pytest.raises(FoldVerificationError, match="holdout label disagrees"):
        verify_fold_run(run, db_path=db)


def test_nonfinite_json_duplicate_keys_and_incomplete_run_rejected(tmp_path):
    db, run = _fixture(tmp_path)
    path = run / "metrics.json"
    path.write_text('{"best_epoch": NaN}', encoding="utf-8")
    _reseal(run)
    with pytest.raises(FoldVerificationError, match="nonfinite JSON"):
        verify_fold_run(run, db_path=db)

    path.write_text('{"best_epoch": 1, "best_epoch": 2}', encoding="utf-8")
    _reseal(run)
    with pytest.raises(FoldVerificationError, match="duplicate JSON key"):
        verify_fold_run(run, db_path=db)
    (run / "sha256.json").unlink()
    with pytest.raises(FoldVerificationError, match="incomplete"):
        verify_fold_run(run, db_path=db)


def test_symlink_artifact_rejected(tmp_path):
    db, run = _fixture(tmp_path)
    original = tmp_path / "checkpoint.pt"
    (run / "model_weights.pt").rename(original)
    (run / "model_weights.pt").symlink_to(original)
    with pytest.raises(FoldVerificationError, match="unsafe artifact"):
        verify_fold_run(run, db_path=db)


def test_different_source_database_rejected_even_if_files_self_consistent(tmp_path):
    db, run = _fixture(tmp_path / "original")
    other_db, other_id = _registered(
        tmp_path / "different", price_shift=0.05
    )
    assert other_id != json.loads((run / "manifest.json").read_text())["dataset_sha256"]
    with pytest.raises(ValueError, match="registered dataset does not exist"):
        verify_fold_run(run, db_path=other_db)


def test_real_training_artifacts_accepted_on_cpu_when_torch_installed(tmp_path):
    pytest.importorskip("torch")
    from emergenzix_gpu_lab.fold_train import train_one_fold

    db_path, dataset_id = _registered(tmp_path)
    out = tmp_path / "real-run"
    train_one_fold(
        db_path=str(db_path), dataset_id=dataset_id, output_dir=str(out),
        fold_number=1, device_name="cpu", model_name="mlp",
        seed=7, epochs=1, batch_size=256, lookback=32, horizon=1,
    )
    result = verify_fold_run(out, db_path=db_path)
    assert result["status"] == "verified_against_source"
