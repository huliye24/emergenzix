"""Prepend a verified historical collection to the current S3 snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3

from .market_data_lake import ROOT_PREFIX, build_lake_bundle, publish_bundle, validate_base_bars
from .market_data_lake_import import DEFAULT_BUCKET, load_collection_bundle
from .market_data_lake_incremental import _read_base


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill BTC history before the current snapshot")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    s3 = boto3.client("s3", region_name=args.region)
    pointer_key = f"{ROOT_PREFIX}/pointers/binance-spot-btcusdt-15m-latest.json"
    pointer_bytes = s3.get_object(Bucket=args.bucket, Key=pointer_key)["Body"].read()
    pointer = json.loads(pointer_bytes)
    manifest = json.loads(
        s3.get_object(Bucket=args.bucket, Key=pointer["manifest_key"])["Body"].read()
    )
    current = _read_base(s3, args.bucket, manifest)
    loaded = load_collection_bundle(args.bundle, allow_gaps=True)
    historical = loaded["bars"]
    if historical[-1]["timestamp"] >= current[0]["timestamp"]:
        raise ValueError("historical bundle overlaps the current snapshot")
    combined = validate_base_bars(historical + current, allow_gaps=True)
    source_objects = [
        {"snapshot_id": pointer["snapshot_id"], "manifest_key": pointer["manifest_key"]},
        *loaded["source_objects"],
    ]
    bundle = build_lake_bundle(
        symbol="BTC/USDT", base_bars=combined, source_objects=source_objects,
        code_version=args.code_version, previous_snapshot=pointer["snapshot_id"],
        allow_gaps=True,
    )
    bundle["objects"].update(loaded["raw_objects"])
    latest_pointer = s3.get_object(Bucket=args.bucket, Key=pointer_key)["Body"].read()
    if latest_pointer != pointer_bytes:
        raise RuntimeError("latest pointer changed during backfill; retry against the new parent")
    publish_bundle(s3_client=s3, bucket=args.bucket, bundle=bundle)
    print(json.dumps({
        "execution_mode": "RESEARCH_ONLY", "status": "published",
        "snapshot_id": bundle["snapshot_id"], "previous_snapshot": pointer["snapshot_id"],
        "base_rows": len(combined), "first_at": combined[0]["timestamp"].isoformat(),
        "last_at": combined[-1]["timestamp"].isoformat(),
        "gaps": bundle["manifest"]["quality"]["gaps"],
        "missing_candles": bundle["manifest"]["quality"]["missing_candles"],
    }, indent=2))


if __name__ == "__main__":
    main()
