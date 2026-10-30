"""Deterministic venue-preserving candle-v2 S3 bundle builder."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import timedelta
from typing import Any, Mapping, Sequence

from botocore.exceptions import ClientError

from .market_candle_v2 import (
    OPTIONAL_VOLUME_FIELDS,
    SCHEMA_VERSION,
    aggregate_candles_v2,
    normalize_candle_v2,
)
from .market_data_lake import _bucket_start, _expected_count


ROOT_PREFIX = "market-data/v2"
BASE_INTERVAL = "15m"
DERIVED_INTERVALS = ("1h", "4h", "1d", "1w", "1mo")


def validate_candles_v2(
    candles: Sequence[Mapping[str, Any]], *, allow_gaps: bool = False
) -> list[dict[str, Any]]:
    normalized = [normalize_candle_v2(item) for item in candles]
    normalized.sort(key=lambda item: item["timestamp"])
    for left, right in zip(normalized, normalized[1:]):
        delta = right["timestamp"] - left["timestamp"]
        if delta <= timedelta(0) or delta % timedelta(minutes=15):
            raise ValueError("candles must be unique and on the 15-minute grid")
        if not allow_gaps and delta != timedelta(minutes=15):
            raise ValueError("candles must be gap-free")
    return normalized


def resample_candles_v2(
    candles: Sequence[Mapping[str, Any]], interval: str, *, allow_gaps: bool = False
) -> list[dict[str, Any]]:
    normalized = validate_candles_v2(candles, allow_gaps=allow_gaps)
    groups: dict[Any, list[dict[str, Any]]] = {}
    for candle in normalized:
        bucket = _bucket_start(candle["timestamp"], interval)
        groups.setdefault(bucket, []).append(candle)
    result = []
    for bucket, members in sorted(groups.items()):
        expected = _expected_count(bucket, interval)
        if len(members) != expected:
            continue
        target = timedelta(minutes=15 * expected)
        try:
            result.append(
                aggregate_candles_v2(
                    members, target_interval=target, expected_start=bucket
                )
            )
        except ValueError:
            continue
    return result


def _parquet_bytes(
    candles: Sequence[Mapping[str, Any]], metadata: Mapping[str, str]
) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    def timestamp_array(field: str):
        return pa.array(
            [int(item[field].timestamp() * 1_000_000) for item in candles],
            type=pa.timestamp("us", tz="UTC"), from_pandas=False,
        )

    table = pa.table({
        "timestamp": timestamp_array("timestamp"),
        "close_time": timestamp_array("close_time"),
        "available_at": timestamp_array("available_at"),
        **{
            field: pa.array(
                [float(item[field]) for item in candles],
                type=pa.float64(), from_pandas=False,
            )
            for field in ("open", "high", "low", "close", "base_volume")
        },
        "quote_volume": pa.array(
            [item["quote_volume"] for item in candles], type=pa.float64(), from_pandas=False
        ),
        "trade_count": pa.array(
            [item["trade_count"] for item in candles], type=pa.int64(), from_pandas=False
        ),
        "taker_buy_base_volume": pa.array(
            [item["taker_buy_base_volume"] for item in candles],
            type=pa.float64(), from_pandas=False,
        ),
        "taker_buy_quote_volume": pa.array(
            [item["taker_buy_quote_volume"] for item in candles],
            type=pa.float64(), from_pandas=False,
        ),
        "is_closed": pa.array([item["is_closed"] for item in candles], type=pa.bool_()),
        "is_imputed": pa.array([item["is_imputed"] for item in candles], type=pa.bool_()),
    }).replace_schema_metadata({key.encode(): value.encode() for key, value in metadata.items()})
    stream = io.BytesIO()
    pq.write_table(table, stream, compression="zstd", version="2.6")
    return stream.getvalue()


def build_lake_bundle_v2(
    *, provider: str, exchange: str, market: str, symbol: str,
    base_candles: Sequence[Mapping[str, Any]],
    source_objects: Sequence[Mapping[str, Any]], code_version: str,
    previous_snapshot: str | None = None, allow_gaps: bool = False,
) -> dict[str, Any]:
    normalized = validate_candles_v2(base_candles, allow_gaps=allow_gaps)
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
        interval: resample_candles_v2(normalized, interval, allow_gaps=allow_gaps)
        for interval in DERIVED_INTERVALS
    })
    objects: dict[str, bytes] = {}
    entries = []
    symbol_key = symbol.lower().replace("/", "")
    for interval, candles in datasets.items():
        zone = "silver" if interval == BASE_INTERVAL else "gold"
        partitions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for candle in candles:
            stamp = candle["timestamp"]
            partitions.setdefault((stamp.year, stamp.month), []).append(candle)
        for (year, month), partition in sorted(partitions.items()):
            payload = _parquet_bytes(partition, {
                "schema_version": SCHEMA_VERSION,
                "provider": provider, "exchange": exchange, "market": market,
                "symbol": symbol, "timeframe": interval,
                "parent_timeframe": BASE_INTERVAL, "code_version": code_version,
            })
            digest = hashlib.sha256(payload).hexdigest()
            key = (
                f"{ROOT_PREFIX}/{zone}/exchange={exchange}/market={market}/"
                f"symbol={symbol_key}/timeframe={interval}/year={year:04d}/"
                f"month={month:02d}/part-{digest}.parquet"
            )
            objects[key] = payload
            entries.append({
                "key": key, "sha256": digest, "bytes": len(payload),
                "rows": len(partition), "timeframe": interval,
                "first_at": partition[0]["timestamp"].isoformat(),
                "last_at": partition[-1]["timestamp"].isoformat(),
            })
    availability = {
        field: sum(item[field] is not None for item in normalized)
        for field in OPTIONAL_VOLUME_FIELDS
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider, "exchange": exchange, "market": market, "symbol": symbol,
        "base_timeframe": BASE_INTERVAL, "derived_timeframes": list(DERIVED_INTERVALS),
        "first_at": normalized[0]["timestamp"].isoformat(),
        "last_at": normalized[-1]["timestamp"].isoformat(),
        "base_rows": len(normalized), "field_non_null_rows": availability,
        "volume_units": {"base_volume": symbol.split("/")[0], "quote_volume": symbol.split("/")[1]},
        "availability_time_policy": "max(interval_close, observed_source_availability)",
        "source_objects": list(source_objects), "objects": entries,
        "code_version": code_version, "previous_snapshot": previous_snapshot,
        "quality": {
            "gaps": len(gaps), "gap_details": gaps,
            "missing_candles": sum(item["missing_candles"] for item in gaps),
            "duplicates": 0, "off_grid": 0, "forming_candle_excluded": True,
            "imputed_rows": sum(item["is_imputed"] for item in normalized),
        },
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    snapshot_id = hashlib.sha256(canonical).hexdigest()
    manifest = {**manifest, "snapshot_id": snapshot_id}
    manifest_key = f"{ROOT_PREFIX}/manifests/snapshot={snapshot_id}/manifest.json"
    objects[manifest_key] = json.dumps(manifest, sort_keys=True, indent=2).encode()
    return {"snapshot_id": snapshot_id, "manifest_key": manifest_key, "manifest": manifest, "objects": objects}


def publish_bundle_v2(*, s3_client: Any, bucket: str, bundle: Mapping[str, Any]) -> None:
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
            Bucket=bucket, Key=key, Body=payload,
            ContentType=("application/json" if key.endswith(".json") else "application/vnd.apache.parquet"),
            Metadata={"sha256": digest}, ServerSideEncryption="AES256",
        )
    manifest = bundle["manifest"]
    pointer_key = (
        f"{ROOT_PREFIX}/pointers/{manifest['exchange']}-{manifest['market']}-"
        f"{manifest['symbol'].lower().replace('/', '')}-15m-latest.json"
    )
    pointer = json.dumps({
        "snapshot_id": bundle["snapshot_id"], "manifest_key": bundle["manifest_key"]
    }, sort_keys=True).encode()
    s3_client.put_object(
        Bucket=bucket, Key=pointer_key, Body=pointer,
        ContentType="application/json", ServerSideEncryption="AES256",
    )
