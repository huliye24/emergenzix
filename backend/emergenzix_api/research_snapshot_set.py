"""Immutable multi-symbol snapshot sets for bounded research experiments."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from botocore.exceptions import ClientError


SCHEMA_VERSION = "emergenzix-research-snapshot-set-v1"
ROOT_PREFIX = "market-data/v2/collections"
REQUIRED_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("snapshot timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def verify_member_manifest(
    manifest: Mapping[str, Any],
    *,
    selected_start: str,
    selected_end_exclusive: str,
) -> dict[str, Any]:
    """Verify one canonical v2 manifest and its selected date coverage."""
    if manifest.get("schema_version") != "emergenzix-market-candle-v2":
        raise ValueError("member must use the candle-v2 schema")
    if manifest.get("exchange") != "binance" or manifest.get("market") != "spot":
        raise ValueError("member must be Binance spot data")
    if manifest.get("base_timeframe") != "15m":
        raise ValueError("member must use canonical 15m candles")
    snapshot_id = str(manifest.get("snapshot_id", ""))
    canonical = _canonical(
        {key: value for key, value in manifest.items() if key != "snapshot_id"}
    )
    if hashlib.sha256(canonical).hexdigest() != snapshot_id:
        raise ValueError("member snapshot hash does not match its canonical manifest")
    start = _utc(selected_start)
    end = _utc(selected_end_exclusive)
    if start >= end:
        raise ValueError("selected range must be non-empty")
    member_start = _utc(str(manifest["first_at"]))
    member_last = _utc(str(manifest["last_at"]))
    if member_start > start or member_last < end:
        raise ValueError(f"member {manifest.get('symbol')} does not cover selected range")
    if not bool(manifest.get("quality", {}).get("forming_candle_excluded")):
        raise ValueError("member does not attest forming-candle exclusion")
    if int(manifest.get("quality", {}).get("imputed_rows", -1)) != 0:
        raise ValueError("member contains imputed rows")
    return {
        "symbol": manifest["symbol"],
        "snapshot_id": snapshot_id,
        "manifest_key": (
            f"market-data/v2/manifests/snapshot={snapshot_id}/manifest.json"
        ),
        "first_at": manifest["first_at"],
        "last_at": manifest["last_at"],
        "base_rows": manifest["base_rows"],
        "gaps": manifest["quality"]["gaps"],
        "missing_candles": manifest["quality"]["missing_candles"],
        "imputed_rows": 0,
    }


def build_snapshot_set(
    *,
    manifests: Sequence[Mapping[str, Any]],
    selected_start: str,
    selected_end_exclusive: str,
    local_mirror_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic three-symbol snapshot-set manifest."""
    if len(local_mirror_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in local_mirror_sha256
    ):
        raise ValueError("local mirror SHA-256 must be lowercase hexadecimal")
    members = [
        verify_member_manifest(
            manifest,
            selected_start=selected_start,
            selected_end_exclusive=selected_end_exclusive,
        )
        for manifest in manifests
    ]
    members.sort(key=lambda item: item["symbol"])
    if tuple(item["symbol"] for item in members) != tuple(sorted(REQUIRED_SYMBOLS)):
        raise ValueError("snapshot set requires exactly BTC/ETH/BNB USDT members")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "mode": "RESEARCH_ONLY",
        "provider": "binance",
        "exchange": "binance",
        "market": "spot",
        "base_timeframe": "15m",
        "selected_start": _utc(selected_start).isoformat(),
        "selected_end_exclusive": _utc(selected_end_exclusive).isoformat(),
        "local_mirror_collection_sha256": local_mirror_sha256,
        "members": members,
        "orders_created": False,
        "automatic_strategy_promotion": False,
        "live_order_permission": False,
    }
    collection_id = hashlib.sha256(_canonical(manifest)).hexdigest()
    manifest = {**manifest, "collection_id": collection_id}
    key = f"{ROOT_PREFIX}/snapshot={collection_id}/manifest.json"
    payload = json.dumps(manifest, sort_keys=True, indent=2).encode()
    return {
        "collection_id": collection_id,
        "manifest_key": key,
        "manifest": manifest,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def publish_snapshot_set(
    *, s3_client: Any, bucket: str, bundle: Mapping[str, Any]
) -> None:
    """Publish one content-addressed object; never write a latest pointer."""
    key = str(bundle["manifest_key"])
    payload = bytes(bundle["payload"])
    digest = hashlib.sha256(payload).hexdigest()
    try:
        existing = s3_client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
    else:
        if existing["Body"].read() != payload:
            raise ValueError("existing snapshot-set object content mismatch")
        return
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json",
        Metadata={"sha256": digest, "execution-mode": "research-only"},
        ServerSideEncryption="AES256",
    )
