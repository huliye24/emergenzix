"""CPU-only chronology tests for GPU walk-forward plan v1."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.schemas import DatasetCreate
from emergenzix_gpu_lab.dataset import load_registered_dataset
from emergenzix_gpu_lab.walk_forward import (
    describe_plan,
    main,
    plan_walk_forward,
)


def test_expanding_folds_purge_labels_and_never_overlap_holdouts():
    windows = plan_walk_forward(1024)
    assert len(windows) == 3
    assert [
        (f.train.bar_stop, f.validation.bar_stop, f.test.bar_stop)
        for f in windows
    ] == [(512, 640, 768), (640, 768, 896), (768, 896, 1024)]
    for f in windows:
        assert f.train.bar_start == 0
        assert f.train.anchor_start == 31  # lookback=32
        assert f.train.last_target < f.validation.bar_start
        assert f.validation.last_target < f.test.bar_start
        assert f.test.last_target < f.test.bar_stop
        assert f.train.sample_count >= 32
        assert f.validation.sample_count == f.test.sample_count == 127
        assert f.train.first_target >= f.train.bar_start
        assert f.validation.first_target >= f.validation.bar_start
        assert f.test.first_target >= f.test.bar_start
    for earlier, later in zip(windows, windows[1:]):
        assert earlier.test.bar_stop <= later.test.bar_start
        assert earlier.test.last_target < later.test.first_target
        # A holdout can become historical training data only in a later fold.
        assert earlier.test.bar_start <= later.train.bar_stop


def test_multi_candle_horizon_purges_every_stage():
    horizon = 12
    result = plan_walk_forward(
        1200, folds=2, train_bars=512, validation_bars=128,
        test_bars=128, step_bars=150, horizon=horizon, lookback=64,
    )
    for f in result:
        assert f.train.anchor_stop == f.train.bar_stop - horizon
        assert f.validation.anchor_stop == f.validation.bar_stop - horizon
        assert f.test.anchor_stop == f.test.bar_stop - horizon
        assert f.train.last_target == f.validation.bar_start - 1
        assert f.validation.last_target == f.test.bar_start - 1
        assert f.test.last_target == f.test.bar_stop - 1
    assert result[0].test.bar_stop < result[1].test.bar_start


@pytest.mark.parametrize("options,match", [
    ({"folds": 1}, "folds"),
    ({"folds": 25}, "folds"),
    ({"lookback": 2}, "lookback"),
    ({"lookback": 257}, "lookback"),
    ({"horizon": 0}, "horizon"),
    ({"horizon": 25}, "horizon"),
    ({"train_bars": 40}, "train_bars"),
    ({"validation_bars": 4}, "validation"),
    ({"test_bars": 4}, "test"),
    ({"step_bars": 100}, "overlap"),
    ({"test_bars": 128, "step_bars": 127}, "overlap"),
    ({"folds": True}, "folds"),
    ({"train_bars": 512.5}, "train_bars"),
])
def test_bad_fold_parameters_fail_closed(options, match):
    with pytest.raises(ValueError, match=match):
        plan_walk_forward(1024, **options)


def test_not_enough_candles_must_not_silently_reduce_fold_count():
    with pytest.raises(ValueError, match="1024 candles required"):
        plan_walk_forward(1023)
    with pytest.raises(ValueError, match="insufficient history"):
        plan_walk_forward(200)


def _make_registered_dataset(tmp_path):
    path = tmp_path / "walkforward.db"
    db = Database(str(path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = [
        {
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": 100.0 + i / 100,
            "high": 101.0 + i / 100,
            "low": 99.0 + i / 100,
            "close": 100.5 + i / 100,
            "volume": float(1000 + i % 17),
        }
        for i in range(1030)
    ]
    row, inserted = persist_dataset(
        db, DatasetCreate(
            symbol="BTC/USDT", timeframe="1h", bars=bars,
            source="synthetic_demo",
        ),
    )
    assert inserted
    return path, row["id"]


def test_verified_dataset_plan_is_read_only_deterministic_and_provenanced(tmp_path):
    path, dataset_id = _make_registered_dataset(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    dataset = load_registered_dataset(path, dataset_id)
    report = describe_plan(dataset)
    assert report == describe_plan(dataset)
    assert report["protocol"] == "gpu-walk-forward-plan-v1"
    assert report["dataset_sha256"] == dataset_id
    assert report["no_order_execution"] is True
    assert report["training_performed"] is False
    assert report["unused_tail_bars"] == 6
    assert len(report["plan_sha256"]) == 64
    f1 = report["windows"][0]
    assert f1["train"]["last_target_at"] == dataset.times[511]
    assert f1["validation"]["first_anchor_at"] == dataset.times[512]
    assert f1["validation"]["last_target_at"] == dataset.times[639]
    assert f1["test"]["first_anchor_at"] == dataset.times[640]
    assert f1["test"]["last_target_at"] == dataset.times[767]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_read_only_cli_prints_same_plan_without_creating_run_artifacts(
    tmp_path, monkeypatch, capsys
):
    path, dataset_id = _make_registered_dataset(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr("sys.argv", [
        "gpu.walk_forward", "--db-path", str(path), "--dataset-id", dataset_id,
        "--folds", "3",
    ])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output["protocol"] == "gpu-walk-forward-plan-v1"
    assert output["training_performed"] is False
    assert output["dataset_sha256"] == dataset_id
    assert len(output["windows"]) == 3
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not any(p.is_dir() for p in tmp_path.iterdir())
    assert not list(tmp_path.rglob("model_weights.pt"))
