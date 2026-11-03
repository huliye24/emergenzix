#!/usr/bin/env python3
"""Publish a pinned BTC/ETH/BNB research snapshot set from live v2 pointers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from emergenzix_api.research_snapshot_set import (  # noqa: E402
    build_snapshot_set,
    publish_snapshot_set,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--local-mirror-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)
    manifests = []
    for symbol in ("btcusdt", "ethusdt", "bnbusdt"):
        pointer_key = (
            f"market-data/v2/pointers/binance-spot-{symbol}-15m-latest.json"
        )
        pointer = json.loads(
            s3.get_object(Bucket=args.bucket, Key=pointer_key)["Body"].read()
        )
        manifest = json.loads(
            s3.get_object(Bucket=args.bucket, Key=pointer["manifest_key"])["Body"].read()
        )
        if pointer["snapshot_id"] != manifest.get("snapshot_id"):
            raise ValueError(f"pointer/manifest mismatch for {symbol}")
        manifests.append(manifest)

    bundle = build_snapshot_set(
        manifests=manifests,
        selected_start=args.start,
        selected_end_exclusive=args.end_exclusive,
        local_mirror_sha256=args.local_mirror_sha256,
    )
    publish_snapshot_set(s3_client=s3, bucket=args.bucket, bundle=bundle)
    stored = s3.get_object(Bucket=args.bucket, Key=bundle["manifest_key"])["Body"].read()
    if stored != bundle["payload"]:
        raise ValueError("published snapshot set failed read-back verification")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(bundle["payload"] + b"\n")
    print(
        json.dumps(
            {
                "mode": "RESEARCH_ONLY",
                "collection_id": bundle["collection_id"],
                "manifest_key": bundle["manifest_key"],
                "payload_sha256": bundle["payload_sha256"],
                "members": [
                    {
                        "symbol": member["symbol"],
                        "snapshot_id": member["snapshot_id"],
                    }
                    for member in bundle["manifest"]["members"]
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
