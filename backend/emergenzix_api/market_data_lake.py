"""Deterministic Parquet/manifest builder and S3 publisher for market data."""

from __future__ import annotations

import hashlib
import io
import json
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from botocore.exceptions import ClientError


BASE_INTERVAL = "15m"
DERIVED_INTERVALS = ("1h", "4h", "1d", "1w", "1mo")
SCHEMA_VERSION = "emergenzix-market-candle-v1"
ROOT_PREFIX = "market-data/v1"


def _timestamp(bar: Mapping[str, Any]) -> datetime:
    value = bar["timestamp"]
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("candle timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _bucket_start(stamp: datetime, interval: str) -> datetime:
    if interval == "1h":
        return stamp.replace(minute=0, second=0, microsecond=0)
    if interval == "4h":
        return stamp.replace(hour=stamp.hour // 4 * 4, minute=0, second=0, microsecond=0)
    if interval == "1d":
        return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "1w":
        day = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    if interval == "1mo":
        return stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported derived interval {interval}")


def _expected_count(bucket: datetime, interval: str) -> int:
    return {
        "1h": 4, "4h": 16, "1d": 96, "1w": 672,
        "1mo": monthrange(bucket.year, bucket.month)[1] * 96,
    }[interval]


def validate_base_bars(
    bars: Sequence[Mapping[str, Any]], *, allow_gaps: bool = False
) -> list[dict[str, Any]]:
    if not bars:
        raise ValueError("base dataset is empty")
    normalized: list[dict[str, Any]] = []
    previous: datetime | None = None
    for source in bars:
        stamp = _timestamp(source)
        if stamp.minute % 15 or stamp.second or stamp.microsecond:
            raise ValueError("base candle is off the UTC 15-minute grid")
        if previous is not None:
            delta = stamp - previous
            if delta <= timedelta(0) or delta % timedelta(minutes=15):
                raise ValueError("base candles must be unique, ordered, and on-grid")
            if not allow_gaps and delta != timedelta(minutes=15):
                raise ValueError("base candles must be unique, ordered, and gap-free")
        values = {key: float(source[key]) for key in ("open", "high", "low", "close", "volume")}
        if min(values[key] for key in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
            raise ValueError("invalid OHLCV value")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise ValueError("invalid candle high")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError("invalid candle low")
        normalized.append({"timestamp": stamp, **values})
        previous = stamp
    return normalized


def resample_bars(
    base_bars: Sequence[Mapping[str, Any]], interval: str, *, allow_gaps: bool = False
) -> list[dict[str, Any]]:
    bars = validate_base_bars(base_bars, allow_gaps=allow_gaps)
    groups: dict[datetime, list[dict[str, Any]]] = {}
    for bar in bars:
        groups.setdefault(_bucket_start(bar["timestamp"], interval), []).append(bar)
    result: list[dict[str, Any]] = []
    for bucket, members in sorted(groups.items()):
        expected = _expected_count(bucket, interval)
        if len(members) != expected:
            continue
        if members[0]["timestamp"] != bucket:
            continue
        if members[-1]["timestamp"] != bucket + timedelta(minutes=15 * (expected - 1)):
            continue
        result.append({
            "timestamp": bucket,
            "open": members[0]["open"], "high": max(item["high"] for item in members),
            "low": min(item["low"] for item in members), "close": members[-1]["close"],
            "volume": sum(item["volume"] for item in members),
        })
    return result


def _parquet_bytes(bars: Sequence[Mapping[str, Any]], metadata: Mapping[str, str]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({
        "timestamp": pa.array(
            [int(_timestamp(bar).timestamp() * 1_000_000) for bar in bars],
            type=pa.timestamp("us", tz="UTC"), from_pandas=False,
        ),
        "open": pa.array([float(bar["open"]) for bar in bars], type=pa.float64(), from_pandas=False),
        "high": pa.array([float(bar["high"]) for bar in bars], type=pa.float64(), from_pandas=False),
        "low": pa.array([float(bar["low"]) for bar in bars], type=pa.float64(), from_pandas=False),
        "close": pa.array([float(bar["close"]) for bar in bars], type=pa.float64(), from_pandas=False),
        "volume": pa.array([float(bar["volume"]) for bar in bars], type=pa.float64(), from_pandas=False),
    }).replace_schema_metadata({key.encode(): value.encode() for key, value in metadata.items()})
    stream = io.BytesIO()
    pq.write_table(table, stream, compression="zstd", version="2.6")
    return stream.getvalue()


def build_lake_bundle(
    *, symbol: str, base_bars: Sequence[Mapping[str, Any]],
    source_objects: Sequence[Mapping[str, Any]], code_version: str,
    previous_snapshot: str | None = None,
    allow_gaps: bool = False,
) -> dict[str, Any]:
    normalized = validate_base_bars(base_bars, allow_gaps=allow_gaps)
    gaps = []
    for previous, current in zip(normalized, normalized[1:]):
        delta = current["timestamp"] - previous["timestamp"]
        if delta != timedelta(minutes=15):
            gaps.append({
                "after": previous["timestamp"].isoformat(),
                "before": current["timestamp"].isoformat(),
                "missing_candles": int(delta / timedelta(minutes=15)) - 1,
            })
    datasets = {BASE_INTERVAL: normalized}
    datasets.update({
        interval: resample_bars(normalized, interval, allow_gaps=allow_gaps)
        for interval in DERIVED_INTERVALS
    })
    objects: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    symbol_key = symbol.lower().replace("/", "")
    for interval, bars in datasets.items():
        zone = "silver" if interval == BASE_INTERVAL else "gold"
        partitions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for bar in bars:
            stamp = _timestamp(bar)
            partitions.setdefault((stamp.year, stamp.month), []).append(bar)
        for (year, month), partition in sorted(partitions.items()):
            payload = _parquet_bytes(partition, {
                "schema_version": SCHEMA_VERSION, "symbol": symbol,
                "timeframe": interval, "parent_timeframe": BASE_INTERVAL,
                "code_version": code_version,
            })
            digest = hashlib.sha256(payload).hexdigest()
            key = (
                f"{ROOT_PREFIX}/{zone}/exchange=binance/market=spot/symbol={symbol_key}/"
                f"timeframe={interval}/year={year:04d}/month={month:02d}/part-{digest}.parquet"
            )
            objects[key] = payload
            entries.append({
                "key": key, "sha256": digest, "bytes": len(payload),
                "rows": len(partition), "timeframe": interval,
                "first_at": _timestamp(partition[0]).isoformat(),
                "last_at": _timestamp(partition[-1]).isoformat(),
            })
    manifest = {
        "schema_version": SCHEMA_VERSION, "provider": "binance",
        "exchange": "binance", "market": "spot", "symbol": symbol,
        "base_timeframe": BASE_INTERVAL, "derived_timeframes": list(DERIVED_INTERVALS),
        "first_at": normalized[0]["timestamp"].isoformat(),
        "last_at": normalized[-1]["timestamp"].isoformat(),
        "base_rows": len(normalized), "source_objects": list(source_objects),
        "objects": entries, "code_version": code_version,
        "previous_snapshot": previous_snapshot,
        "quality": {
            "gaps": len(gaps), "gap_details": gaps,
            "missing_candles": sum(item["missing_candles"] for item in gaps),
            "duplicates": 0, "off_grid": 0, "forming_candle_excluded": True,
        },
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    snapshot_id = hashlib.sha256(canonical).hexdigest()
    manifest = {**manifest, "snapshot_id": snapshot_id}
    manifest_key = f"{ROOT_PREFIX}/manifests/snapshot={snapshot_id}/manifest.json"
    objects[manifest_key] = json.dumps(manifest, sort_keys=True, indent=2).encode()
    return {"snapshot_id": snapshot_id, "manifest_key": manifest_key, "manifest": manifest, "objects": objects}


def publish_bundle(*, s3_client: Any, bucket: str, bundle: Mapping[str, Any]) -> None:
    for key, payload in bundle["objects"].items():
        digest = hashlib.sha256(payload).hexdigest()
        try:
            existing = s3_client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise
        else:
            if existing.get("Metadata", {}).get("sha256") != digest:
                raise ValueError(f"existing S3 object metadata mismatch for {key}")
            continue
        s3_client.put_object(
            Bucket=bucket, Key=key, Body=payload, ContentType=(
                "application/json" if key.endswith(".json") else "application/vnd.apache.parquet"
            ), Metadata={"sha256": digest}, ServerSideEncryption="AES256",
        )
    pointer_key = (
        f"{ROOT_PREFIX}/pointers/binance-spot-"
        f"{bundle['manifest']['symbol'].lower().replace('/', '')}-15m-latest.json"
    )
    pointer = json.dumps({
        "snapshot_id": bundle["snapshot_id"], "manifest_key": bundle["manifest_key"]
    }, sort_keys=True).encode()
    s3_client.put_object(
        Bucket=bucket, Key=pointer_key, Body=pointer,
        ContentType="application/json", ServerSideEncryption="AES256",
    )
