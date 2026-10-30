"""Build and publish verified BTC/ETH/BNB multi-timeframe S3 snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import boto3

from .market_data_lake import ROOT_PREFIX, build_lake_bundle, publish_bundle
from .market_history import SYMBOLS, build_binance_verified_history


DEFAULT_BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"


def build_symbol_bundle(*, symbol: str, start_at: datetime, code_version: str) -> dict[str, Any]:
    bronze: dict[str, bytes] = {}
    source_objects: list[dict[str, Any]] = []

    def observe(url: str, payload: bytes, checksum_text: str, digest: str) -> None:
        filename = url.rsplit("/", 1)[-1]
        match = re.search(r"-(\d{4})-(\d{2})(?:-\d{2})?\.zip$", filename)
        if match is None:
            raise ValueError(f"unrecognized Binance archive filename {filename}")
        year, month = match.groups()
        symbol_key = symbol.lower().replace("/", "")
        prefix = (
            f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=klines/"
            f"symbol={symbol_key}/interval=15m/year={year}/month={month}"
        )
        zip_key = f"{prefix}/sha256={digest}/{filename}"
        checksum_key = f"{zip_key}.CHECKSUM"
        bronze[zip_key] = payload
        bronze[checksum_key] = checksum_text.encode()
        source_objects.append({
            "key": zip_key, "sha256": digest, "bytes": len(payload),
            "checksum_key": checksum_key, "source_url": url,
        })

    history = build_binance_verified_history(
        symbol=symbol, interval="15m", start_at=start_at,
        archive_observer=observe,
    )
    public_tail = json.dumps(history["bars"][-1000:], default=str, sort_keys=True).encode()
    tail_digest = hashlib.sha256(public_tail).hexdigest()
    tail_key = (
        f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=public-tail/"
        f"symbol={symbol.lower().replace('/', '')}/interval=15m/tail-{tail_digest}.json"
    )
    bronze[tail_key] = public_tail
    source_objects.append({
        "key": tail_key, "sha256": tail_digest, "bytes": len(public_tail),
        "source": "ccxt_binance_public_ohlcv", "overlap_bars": history["overlap_bars"],
    })
    bundle = build_lake_bundle(
        symbol=str(history["symbol"]), base_bars=history["bars"],
        source_objects=source_objects, code_version=code_version,
    )
    bundle["objects"].update(bronze)
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish verified market-data lake snapshots")
    parser.add_argument("--symbols", nargs="+", choices=sorted(SYMBOLS), default=sorted(SYMBOLS))
    parser.add_argument("--start", default="2021-01-01T00:00:00Z")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    if start.tzinfo is None or start.utcoffset() is None:
        raise SystemExit("--start must include a timezone offset")
    client = boto3.client("s3", region_name=args.region)
    reports = []
    for symbol in args.symbols:
        bundle = build_symbol_bundle(
            symbol=symbol, start_at=start.astimezone(timezone.utc),
            code_version=args.code_version,
        )
        publish_bundle(s3_client=client, bucket=args.bucket, bundle=bundle)
        reports.append({
            "symbol": symbol, "snapshot_id": bundle["snapshot_id"],
            "manifest_key": bundle["manifest_key"],
            "objects": len(bundle["objects"]),
        })
    print(json.dumps({"execution_mode": "RESEARCH_ONLY", "published": reports}, indent=2))


if __name__ == "__main__":
    main()
