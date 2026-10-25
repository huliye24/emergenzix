"""Offline GPU Alpha Lab leakage, provenance and optional CPU smoke tests."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.schemas import DatasetCreate
from emergenzix_gpu_lab.dataset import load_registered_dataset, make_samples


def _registered_dataset(tmp_path, *, n=240, timeframe="4h"):
    path = tmp_path / "research.db"
    db = Database(str(path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    step = {"1h": 3600, "4h": 14400, "1d": 86400}[timeframe]
    bars = []
    for i in range(n):
        close = 100 + i * 0.04 + 1.5 * math.sin(i / 7)
        bars.append({
            "timestamp": (start + timedelta(seconds=i * step)).isoformat(),
            "open": close * 0.999, "high": close * 1.01,
            "low": close * 0.99, "close": close,
            "volume": float(1000 + i % 13),
        })
    row, inserted = persist_dataset(
        db, DatasetCreate(symbol="BTC/USDT", timeframe=timeframe, bars=bars)
    )
    assert inserted
    return path, row["id"]


def test_registered_dataset_has_verified_id_and_read_only_sqlite(tmp_path):
    path, dataset_id = _registered_dataset(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = load_registered_dataset(path, dataset_id)
    assert data.dataset_id == dataset_id
    assert data.symbol == "BTC/USDT"
    assert data.timeframe == "4h"
    assert len(data.close) == 240
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_db_is_never_created_and_ids_are_validated(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        load_registered_dataset(missing, "0" * 64)
    assert not missing.exists()
    with pytest.raises(ValueError, match="64 lowercase"):
        load_registered_dataset(missing, "../../escape")


def test_modified_registered_candle_is_rejected_by_existing_verifier(tmp_path):
    path, dataset_id = _registered_dataset(tmp_path)
    with sqlite3.connect(path) as conn:
        bars = json.loads(conn.execute(
            "SELECT bars_json FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()[0])
        bars[20]["close"] += 0.01
        conn.execute("UPDATE market_datasets SET bars_json = ? WHERE id = ?",
                     (json.dumps(bars), dataset_id))
    with pytest.raises(ValueError, match="integrity"):
        load_registered_dataset(path, dataset_id)


def test_time_gap_is_detected_even_with_a_self_consistent_fingerprint(tmp_path):
    path, dataset_id = _registered_dataset(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        bars = json.loads(row["bars_json"])
        bars[32]["timestamp"] = "2024-01-06T12:00:00.000000Z"
        canonical = json.dumps({
            "source": row["source"], "exchange": row["exchange"],
            "symbol": row["symbol"], "timeframe": row["timeframe"],
            "bars": bars,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False)
        new_id = hashlib.sha256(canonical.encode()).hexdigest()
        conn.execute(
            "UPDATE market_datasets SET id = ?, content_sha256 = ?, bars_json = ? WHERE id = ?",
            (new_id, new_id, json.dumps(bars), dataset_id),
        )
    with pytest.raises(ValueError, match="incomplete or non-monotonic"):
        load_registered_dataset(path, new_id)


def test_purged_folds_and_future_changes_cannot_modify_training(tmp_path):
    path, dataset_id = _registered_dataset(tmp_path, n=300)
    data = load_registered_dataset(path, dataset_id)
    split = make_samples(data, lookback=16, horizon=6)
    assert split.train_anchors[-1] + 6 < split.train_cut
    assert split.val_anchors[0] >= split.train_cut
    assert split.val_anchors[-1] + 6 < split.val_cut
    assert split.test_anchors[0] >= split.val_cut
    assert split.test_anchors[-1] + 6 < len(data.close)

    # Change test-only prices, including return features on their boundary.
    data.close[split.val_cut + 10:] *= 2
    changed = make_samples(data, lookback=16, horizon=6)
    np.testing.assert_array_equal(split.feature_mean, changed.feature_mean)
    np.testing.assert_array_equal(split.feature_std, changed.feature_std)
    np.testing.assert_array_equal(split.train_x, changed.train_x)
    np.testing.assert_array_equal(split.train_y, changed.train_y)
    assert not np.array_equal(split.test_y, changed.test_y)


def test_short_sequences_and_invalid_lookback_are_rejected(tmp_path):
    path, dataset_id = _registered_dataset(tmp_path, n=120)
    data = load_registered_dataset(path, dataset_id)
    with pytest.raises(ValueError, match="insufficient history"):
        make_samples(data, lookback=100, horizon=12)
    with pytest.raises(ValueError, match="lookback"):
        make_samples(data, lookback=2)


def test_cpu_training_saves_validated_artifacts_and_rejects_silent_fallback(tmp_path):
    torch = pytest.importorskip("torch")
    from emergenzix_gpu_lab.train import train_experiment

    path, dataset_id = _registered_dataset(tmp_path)
    output = tmp_path / "run-001"
    result = train_experiment(
        db_path=str(path), dataset_id=dataset_id, output_dir=str(output),
        model_name="mlp", device_name="cpu", seed=123, epochs=2,
        lookback=16, horizon=2, batch_size=128,
    )
    assert result["device"] == "cpu"
    manifest = json.loads((output / "manifest.json").read_text())
    metrics = json.loads((output / "metrics.json").read_text())
    predictions = json.loads((output / "test_predictions.json").read_text())
    checksums = json.loads((output / "sha256.json").read_text())
    assert manifest["research_only"] and manifest["no_order_execution"]
    assert manifest["dataset_sha256"] == dataset_id
    assert len(predictions) == manifest["test_examples"]
    assert 1 <= metrics["best_epoch"] <= 2
    assert "zero_return_baseline" in metrics
    assert set(checksums) == {
        "model_weights.pt", "manifest.json", "metrics.json", "test_predictions.json",
    }
    for filename, digest in checksums.items():
        assert hashlib.sha256((output / filename).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        train_experiment(
            db_path=str(path), dataset_id=dataset_id, output_dir=str(output),
            device_name="cpu", epochs=1,
        )
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError, match="CUDA explicitly requested"):
            train_experiment(
                db_path=str(path), dataset_id=dataset_id,
                output_dir=str(tmp_path / "missing-gpu"), device_name="cuda", epochs=1,
            )
        assert not (tmp_path / "missing-gpu").exists()


def test_tcn_cannot_observe_future_timesteps():
    torch = pytest.importorskip("torch")
    from emergenzix_gpu_lab.models import make_model
    torch.manual_seed(123)
    model = make_model("tcn", lookback=24, features=4)
    model.eval()
    input_x = torch.randn(2, 24, 4)
    with torch.no_grad():
        output = model(input_x)
        early = model.layers(input_x.transpose(1, 2))
        altered = input_x.clone()
        altered[:, 20:, :] += 99
        future_modified = model.layers(altered.transpose(1, 2))
    assert tuple(output.shape) == (2,)
    torch.testing.assert_close(early[:, :, :20], future_modified[:, :, :20])
