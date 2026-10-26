"""Parity-safe export tests. No exchange network, Docker or trading engine run."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from emergenzix_api.database import Database
from emergenzix_api.dataset_store import persist_dataset
from emergenzix_api.freqtrade_dataset_bridge import (
    FREQTRADE_FILE, MANIFEST_FILE, export_verified_dataset,
    verify_exported_dataset,
)
from emergenzix_api.freqtrade_research_cli import validate_shared_data_window
from emergenzix_api.schemas import Bar, DatasetCreate

UTC_NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def _dataset(
    tmp_path: Path, *, count: int = 240, break_gap: bool = False,
    source: str = "binance_public_archive", exchange: str = "binance",
) -> tuple[Path, str]:
    db_path = tmp_path / "research.sqlite3"
    db = Database(str(db_path))
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(count):
        bar_time = start + timedelta(hours=4 * (i + (1 if break_gap and i >= 100 else 0)))
        open_price = 100 + i
        candles.append(Bar(
            timestamp=bar_time, open=open_price,
            high=open_price + 2, low=open_price - 2,
            close=open_price + 0.5, volume=10.0,
        ))
    record, _ = persist_dataset(db, DatasetCreate(
        source=source, exchange=exchange,
        symbol="BTCUSDT", timeframe="4h", bars=candles,
    ))
    return db_path, record["id"]


def _folder(tmp_path: Path) -> Path:
    return tmp_path / "ops" / "freqtrade" / "user_data" / "data" / "binance"


def test_verified_bridge_exports_freqtrade_positional_json_and_manifest(tmp_path: Path) -> None:
    db_path, dataset_id = _dataset(tmp_path)
    folder = _folder(tmp_path)
    manifest = export_verified_dataset(
        db_path=db_path, dataset_id=dataset_id, data_dir=folder,
        utc_now=UTC_NOW,
    )
    raw = (folder / FREQTRADE_FILE).read_bytes()
    rows = json.loads(raw)
    assert len(rows) == 240
    assert rows[0] == [1735689600000, 100.0, 102.0, 98.0, 100.5, 10.0]
    assert rows[1][0] - rows[0][0] == 4 * 3600 * 1000
    assert manifest["data_sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["dataset_id"] == dataset_id
    assert manifest["bars_count"] == 240
    assert manifest["time_unit"] == "unix_epoch_milliseconds"
    assert manifest["source_attestation"] == (
        "declared_metadata_plus_dataset_integrity_only"
    )
    assert json.loads((folder / MANIFEST_FILE).read_text()) == manifest
    assert export_verified_dataset(
        db_path=db_path, dataset_id=dataset_id, data_dir=folder,
        utc_now=UTC_NOW,
    ) == manifest  # identical export is safely idempotent


def test_reject_modified_freqtrade_data_or_tampered_manifest(tmp_path: Path) -> None:
    db_path, dataset_id = _dataset(tmp_path)
    folder = _folder(tmp_path)
    export_verified_dataset(db_path=db_path, dataset_id=dataset_id, data_dir=folder)
    path = folder / FREQTRADE_FILE
    original = path.read_bytes()
    path.write_bytes(original.replace(b"100.5", b"900.5", 1))
    with pytest.raises(ValueError, match="differs"):
        verify_exported_dataset(db_path=db_path, dataset_id=dataset_id, data_dir=folder)
    with pytest.raises(ValueError, match="differs"):
        export_verified_dataset(db_path=db_path, dataset_id=dataset_id, data_dir=folder)

    path.write_bytes(original)
    saved = folder / MANIFEST_FILE
    altered = json.loads(saved.read_text())
    altered["dataset_id"] = "0" * 64
    saved.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="manifest"):
        verify_exported_dataset(db_path=db_path, dataset_id=dataset_id, data_dir=folder)


def test_reject_gap_in_strict_4h_sequence(tmp_path: Path) -> None:
    db_path, dataset_id = _dataset(tmp_path, break_gap=True)
    with pytest.raises(ValueError, match="gap"):
        export_verified_dataset(
            db_path=db_path, dataset_id=dataset_id, data_dir=_folder(tmp_path)
        )
    assert not _folder(tmp_path).exists()


def test_reject_untrusted_asset_source_metadata(tmp_path: Path) -> None:
    db_path, dataset_id = _dataset(tmp_path, source="synthetic_demo")
    with pytest.raises(ValueError, match="declared Binance"):
        export_verified_dataset(
            db_path=db_path, dataset_id=dataset_id, data_dir=_folder(tmp_path)
        )


def test_reject_nonexistent_database_or_invalid_dataset_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing"):
        export_verified_dataset(
            db_path=tmp_path / "absent.db", dataset_id="a" * 64,
            data_dir=_folder(tmp_path),
        )
    db_path, _ = _dataset(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        export_verified_dataset(
            db_path=db_path, dataset_id="../../bad", data_dir=_folder(tmp_path),
        )


def test_incomplete_bars_not_exported(tmp_path: Path) -> None:
    db_path, dataset_id = _dataset(tmp_path)
    last = datetime(2025, 2, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="incomplete"):
        export_verified_dataset(
            db_path=db_path, dataset_id=dataset_id, data_dir=_folder(tmp_path),
            utc_now=last - timedelta(hours=1),
        )


def test_upstream_preflight_checks_same_db_fingerprint_and_200_startup_candles(
    tmp_path: Path,
) -> None:
    db_path, dataset_id = _dataset(tmp_path)
    folder = _folder(tmp_path)
    export_verified_dataset(db_path=db_path, dataset_id=dataset_id, data_dir=folder)
    manifest = validate_shared_data_window(
        root=tmp_path, db_path=db_path, dataset_id=dataset_id,
        start=date(2025, 2, 4), end=date(2025, 2, 9),
    )
    assert manifest["bars_count"] == 240
    with pytest.raises(ValueError, match="200 startup"):
        validate_shared_data_window(
            root=tmp_path, db_path=db_path, dataset_id=dataset_id,
            start=date(2025, 1, 20), end=date(2025, 2, 9),
        )
    with pytest.raises(ValueError, match="full test window"):
        validate_shared_data_window(
            root=tmp_path, db_path=db_path, dataset_id=dataset_id,
            start=date(2025, 2, 4), end=date(2025, 2, 12),
        )


def test_runner_blocks_mismatched_dataset_arguments_before_docker(
    monkeypatch, tmp_path: Path,
) -> None:
    import emergenzix_api.freqtrade_research_cli as cli

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        cli, "validate_upstream_environment",
        lambda root: (
            root / "ops/freqtrade/docker-compose.research.yml",
            root / "ops/freqtrade/user_data",
        ),
    )

    def never_run(*_args, **_kwargs):
        pytest.fail("Docker must not start when dataset flags are incomplete")

    monkeypatch.setattr(cli.subprocess, "run", never_run)
    assert cli.main([
        "--capital", "100", "--period", "1w", "--start", "2025-10-21",
        "--dataset-id", "a" * 64,
    ]) == 2
