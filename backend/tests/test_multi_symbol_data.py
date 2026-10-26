"""Tests for the multi-symbol data interface and its verified archive cache."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from emergenzix_api import historical_data as hd


def test_symbol_validation_rejects_unsafe_input() -> None:
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for bad in ("", "btc", "BTC/USDT", "../BTCUSDT", "B", "A" * 21, "BTC USDT"):
        with pytest.raises(ValueError):
            hd.fetch_binance_spot_4h_archive(symbol=bad, start_at=stamp, end_at=stamp)


def test_symbol_validation_accepts_lowercase_and_strips(tmp_path: Path) -> None:
    # Validation happens before any network access, so a bad range still proves
    # the symbol normalised instead of raising a symbol error.
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="end_at must be later"):
        hd.fetch_binance_spot_4h_archive(symbol=" ethusdt ", start_at=stamp,
                                         end_at=stamp, cache_dir=tmp_path)


def test_cache_directory_resolution(tmp_path: Path, monkeypatch) -> None:
    assert hd.cache_directory(None) is None
    assert hd.cache_directory(tmp_path) == tmp_path
    monkeypatch.setenv(hd.CACHE_DIR_ENV, str(tmp_path))
    assert hd.cache_directory(None) == tmp_path
    monkeypatch.setenv(hd.CACHE_DIR_ENV, "   ")
    assert hd.cache_directory(None) is None


def test_cache_roundtrip_and_integrity(tmp_path: Path) -> None:
    payload = b"PK\x03\x04 pretend zip bytes"
    hd._store_cached(tmp_path, "ETHUSDT", "monthly", "2024-01", payload)
    assert hd._load_cached(tmp_path, "ETHUSDT", "monthly", "2024-01") == payload

    zip_path, digest_path = hd._cache_files(tmp_path, "ETHUSDT", "monthly", "2024-01")
    assert zip_path.is_file() and digest_path.is_file()
    assert digest_path.read_text(encoding="utf-8").strip() == hashlib.sha256(payload).hexdigest()

    # A tampered archive must be treated as a cache miss, not as valid data.
    zip_path.write_bytes(payload + b"tampered")
    assert hd._load_cached(tmp_path, "ETHUSDT", "monthly", "2024-01") is None


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    assert hd._load_cached(tmp_path, "ETHUSDT", "monthly", "1999-01") is None
    assert hd._load_cached(None, "ETHUSDT", "monthly", "2024-01") is None


def test_cache_store_failure_is_swallowed(tmp_path: Path) -> None:
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x", encoding="utf-8")
    # Should not raise even though the cache path cannot be created.
    hd._store_cached(blocked, "ETHUSDT", "monthly", "2024-01", b"payload")


def test_btc_wrapper_still_exists() -> None:
    assert callable(hd.fetch_binance_btc_4h_archive)
