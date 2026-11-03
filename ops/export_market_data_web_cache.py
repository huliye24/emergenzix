"""Export hash-verified S3 Gold candles for the legacy read-only web cache."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path

import boto3
import pyarrow.parquet as pq

BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"
POINTER = "market-data/v2/pointers/binance-spot-btcusdt-15m-latest.json"


def _serializable(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _pick(row: dict[str, object], *names: str) -> object:
    for name in names:
        if name in row:
            return row[name]
    raise KeyError(f"missing columns {names}; available={sorted(row)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeframes", nargs="+", default=["1h", "4h", "1d"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    s3 = boto3.client("s3", region_name="cn-northwest-1")
    pointer = json.loads(s3.get_object(Bucket=BUCKET, Key=POINTER)["Body"].read())
    manifest_bytes = s3.get_object(
        Bucket=BUCKET, Key=pointer["manifest_key"]
    )["Body"].read()
    manifest = json.loads(manifest_bytes)

    for timeframe in args.timeframes:
        candles: list[dict[str, object]] = []
        objects = [
            item for item in manifest["objects"]
            if item.get("timeframe") == timeframe
        ]
        for item in objects:
            payload = s3.get_object(Bucket=BUCKET, Key=item["key"])["Body"].read()
            if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise RuntimeError(f"hash mismatch: {item['key']}")
            for row in pq.read_table(io.BytesIO(payload)).to_pylist():
                candles.append(
                    {
                        "openTime": _serializable(
                            _pick(row, "open_time", "open_at", "timestamp")
                        ),
                        "open": float(_pick(row, "open")),
                        "high": float(_pick(row, "high")),
                        "low": float(_pick(row, "low")),
                        "close": float(_pick(row, "close")),
                        "volume": float(_pick(row, "base_volume", "volume")),
                    }
                )

        candles.sort(key=lambda item: str(item["openTime"]))
        document = {
            "schema": "emergenzix-web-lake-cache-v1",
            "source": "s3_data_lake_gold",
            "bucket": BUCKET,
            "snapshotId": pointer["snapshot_id"],
            "manifestKey": pointer["manifest_key"],
            "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "exchange": manifest["exchange"],
            "market": manifest["market"],
            "symbol": manifest["symbol"],
            "timeframe": timeframe,
            "firstAt": candles[0]["openTime"],
            "lastAt": candles[-1]["openTime"],
            "count": len(candles),
            "candles": candles,
        }
        target = args.output / f"binance-btcusdt-{timeframe}.json.gz"
        with gzip.open(target, "wb", compresslevel=9) as handle:
            handle.write(
                json.dumps(document, separators=(",", ":")).encode("utf-8")
            )
        print(
            json.dumps(
                {
                    "file": str(target),
                    "rows": len(candles),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        )


if __name__ == "__main__":
    main()
