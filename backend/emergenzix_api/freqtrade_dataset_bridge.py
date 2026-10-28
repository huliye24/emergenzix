"""Export *one verified registered* BTC spot 4h dataset to Freqtrade JSON.

This is a format/provenance bridge, not a new market-data or backtest engine.
Both research engines can now use the same content-addressed K-line dataset.
The source tag is metadata, NOT an independent exchange authenticity proof.
No exchange network, strategy evaluation, API credentials or orders are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .dataset_store import DatasetIntegrityError, verify_stored_dataset

INTERVAL = timedelta(hours=4)
FREQTRADE_FILE = "BTC_USDT-4h.json"
MANIFEST_FILE = "BTC_USDT-4h.emergenzix-manifest.json"
EXPORT_CONTRACT = "emergenzix-freqtrade-spot-4h-v1"


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ensure_ascii=True,
    )


def _read_dataset(db_path: Path, dataset_id: str) -> tuple[Any, list[dict[str, Any]]]:
    if (
        not isinstance(dataset_id, str)
        or len(dataset_id) != 64
        or any(ch not in "0123456789abcdef" for ch in dataset_id)
    ):
        raise ValueError("dataset_id must be a lowercase SHA-256 identifier")
    if not db_path.is_file():
        raise ValueError("existing Emergenzix database required; refusing to create one")
    try:
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM market_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            if row is None:
                raise ValueError("dataset_id not found")
            bars = verify_stored_dataset(row)
            # Do not export unrelated assets/markets into a BTC spot dataset.
            if (
                row["exchange"] != "binance"
                or row["symbol"] not in ("BTCUSDT", "BTC/USDT")
                or row["timeframe"] != "4h"
                or row["source"] != "binance_public_archive"
            ):
                raise ValueError(
                    "comparison bridge requires declared Binance BTC/USDT spot 4h archive"
                )
            return dict(row), bars
    except (sqlite3.Error, DatasetIntegrityError) as exc:
        raise ValueError("could not verify immutable BTC source dataset") from exc


def _as_freqtrade(
    bars: list[dict[str, Any]], *, utc_now: datetime | None = None,
) -> list[list[int | float]]:
    now = utc_now if utc_now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("utc_now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    result: list[list[int | float]] = []
    previous = None
    for bar in bars:
        try:
            timestamp = datetime.fromisoformat(str(bar["timestamp"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("missing timezone")
            timestamp = timestamp.astimezone(timezone.utc)
            prices = tuple(float(bar[key]) for key in ("open", "high", "low", "close", "volume"))
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError("invalid stored 4h candle") from exc
        o, h, l, c, v = prices
        if (
            not all(math.isfinite(x) for x in prices)
            or min(o, h, l, c) <= 0
            or v < 0
            or h < max(o, l, c)
            or l > min(o, h, c)
        ):
            raise ValueError("OHLCV price/volume invariant failed")
        if (
            timestamp.hour % 4 != 0
            or timestamp.minute != 0
            or timestamp.second != 0
            or timestamp.microsecond != 0
        ):
            raise ValueError("4h candles must open on UTC 00/04/08/12/16/20")
        if previous is not None and timestamp - previous != INTERVAL:
            raise ValueError("gap, duplicate or nonmonotonic 4h candle detected")
        if timestamp + INTERVAL > now:
            raise ValueError("incomplete or future 4h candle; refuse to export")
        result.append([int(timestamp.timestamp()) * 1000, o, h, l, c, v])
        previous = timestamp
    if len(result) < 5:
        raise ValueError("at least five complete 4h candles are required")
    return result


def _make_export(
    db_path: Path, dataset_id: str, *, utc_now: datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    row, bars = _read_dataset(db_path, dataset_id)
    ft_rows = _as_freqtrade(bars, utc_now=utc_now)
    data = (json.dumps(ft_rows, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    file_sha = hashlib.sha256(data).hexdigest()
    start = datetime.fromtimestamp(ft_rows[0][0] / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(ft_rows[-1][0] / 1000, tz=timezone.utc)
    manifest = {
        "contract": EXPORT_CONTRACT,
        "dataset_id": dataset_id,
        "dataset_sha256": row["content_sha256"],
        "declared_source": row["source"],
        "exchange": "binance",
        "pair": "BTC/USDT",
        "timeframe": "4h",
        "candle_type": "spot",
        "data_filename": FREQTRADE_FILE,
        "data_sha256": file_sha,
        "bars_count": len(ft_rows),
        "first_open_at_utc": start.isoformat().replace("+00:00", "Z"),
        "last_open_at_utc": last.isoformat().replace("+00:00", "Z"),
        "last_close_exclusive_utc": (last + INTERVAL).isoformat().replace("+00:00", "Z"),
        "timeframe_seconds": 14400,
        "time_unit": "unix_epoch_milliseconds",
        "source_attestation": "declared_metadata_plus_dataset_integrity_only",
    }
    return data, manifest


def _atomic_write(path: Path, payload: bytes) -> None:
    # Atomic per-file writes. If interrupted between files, verification blocks use.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ft-export-",
                                     delete=False) as tmp:
        staged = Path(tmp.name)
        try:
            tmp.write(payload)
            tmp.flush()
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def verify_exported_dataset(
    *, db_path: Path, dataset_id: str, data_dir: Path,
    utc_now: datetime | None = None,
) -> dict[str, Any]:
    """Revalidate the source DB AND exact expected Freqtrade file before any run."""
    wanted_data, wanted_manifest = _make_export(db_path, dataset_id, utc_now=utc_now)
    data_path, manifest_path = data_dir / FREQTRADE_FILE, data_dir / MANIFEST_FILE
    if not data_path.is_file() or not manifest_path.is_file():
        raise ValueError("missing matching Freqtrade data and provenance manifest")
    if hashlib.sha256(data_path.read_bytes()).digest() != hashlib.sha256(wanted_data).digest():
        raise ValueError("Freqtrade file differs from verified source dataset")
    try:
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read Freqtrade provenance manifest") from exc
    if saved != wanted_manifest:
        raise ValueError("Freqtrade manifest does not match verified dataset")
    return wanted_manifest


def export_verified_dataset(
    *, db_path: Path, dataset_id: str, data_dir: Path,
    utc_now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed on existing conflicting source data; never overwrite it."""
    content, manifest = _make_export(db_path, dataset_id, utc_now=utc_now)
    data_path, manifest_path = data_dir / FREQTRADE_FILE, data_dir / MANIFEST_FILE
    if data_path.exists() or manifest_path.exists():
        # Idempotent only when both contents match the immutable source.
        return verify_exported_dataset(
            db_path=db_path, dataset_id=dataset_id, data_dir=data_dir,
            utc_now=utc_now,
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(data_path, content)
    _atomic_write(
        manifest_path, (_canonical(manifest) + "\n").encode("utf-8"),
    )
    return verify_exported_dataset(
        db_path=db_path, dataset_id=dataset_id, data_dir=data_dir,
        utc_now=utc_now,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export verified Binance 4h candles to Freqtrade")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    data_dir = root / "ops" / "freqtrade" / "user_data" / "data" / "binance"
    try:
        manifest = export_verified_dataset(
            db_path=args.db_path, dataset_id=args.dataset_id, data_dir=data_dir,
        )
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(_canonical({"status": "exported_and_verified", **manifest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
