"""Idempotently advance a Binance candle-v2 snapshot with closed public bars."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import boto3
import pyarrow.parquet as pq

from .market_data_lake_v2 import ROOT_PREFIX, build_lake_bundle_v2, publish_bundle_v2
from .market_history import fetch_binance_public_tail


DEFAULT_BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"
SYMBOLS = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "BNBUSDT": "BNB/USDT"}
FIELDS = (
    "open", "high", "low", "close", "base_volume", "quote_volume",
    "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
)


def read_base_v2(s3_client: Any, bucket: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    candles = []
    for item in manifest["objects"]:
        if item["timeframe"] != "15m":
            continue
        payload = s3_client.get_object(Bucket=bucket, Key=item["key"])["Body"].read()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"stored Parquet hash mismatch for {item['key']}")
        columns = pq.read_table(io.BytesIO(payload)).to_pydict()
        for index, stamp in enumerate(columns["timestamp"]):
            candles.append({field: columns[field][index] for field in columns})
    return sorted(candles, key=lambda item: item["timestamp"])


def merge_closed_tail(
    stored: list[dict[str, Any]], raw_tail: list[dict[str, Any]], *,
    completed: datetime, observed_at: datetime,
) -> tuple[list[dict[str, Any]], int, int]:
    by_time = {item["timestamp"]: item for item in stored}
    tail = []
    for item in raw_tail:
        opened = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        if opened > completed:
            continue
        tail.append({
            "timestamp": opened, "close_time": opened + timedelta(minutes=15),
            "available_at": max(opened + timedelta(minutes=15), observed_at),
            **{field: item[field] for field in FIELDS},
            "is_closed": True, "is_imputed": False,
        })
    overlap = [item for item in tail if item["timestamp"] in by_time]
    if len(overlap) < 6:
        raise ValueError("incremental tail has insufficient overlap")
    for item in overlap:
        previous = by_time[item["timestamp"]]
        if any(previous[field] != item[field] for field in FIELDS):
            raise ValueError(f"incremental source mismatch at {item['timestamp'].isoformat()}")
    new_tail = [item for item in tail if item["timestamp"] > stored[-1]["timestamp"]]
    if new_tail:
        sequence = [stored[-1], *new_tail]
        if any(
            right["timestamp"] - left["timestamp"] != timedelta(minutes=15)
            for left, right in zip(sequence, sequence[1:])
        ):
            raise ValueError("incremental tail would introduce a new gap")
        by_time.update({item["timestamp"]: item for item in new_tail})
    return [by_time[stamp] for stamp in sorted(by_time)], len(new_tail), len(overlap)


def update_once_v2(
    *, s3_client: Any, bucket: str, code_version: str, symbol: str = "BTCUSDT",
    tail_fetcher: Callable[..., dict[str, Any]] = fetch_binance_public_tail,
    now: datetime | None = None,
) -> dict[str, Any]:
    if symbol not in SYMBOLS:
        raise ValueError(f"unsupported symbol; choose one of {sorted(SYMBOLS)}")
    pointer_key = f"{ROOT_PREFIX}/pointers/binance-spot-{symbol.lower()}-15m-latest.json"
    pointer = json.loads(s3_client.get_object(Bucket=bucket, Key=pointer_key)["Body"].read())
    manifest = json.loads(
        s3_client.get_object(Bucket=bucket, Key=pointer["manifest_key"])["Body"].read()
    )
    stored = read_base_v2(s3_client, bucket, manifest)
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    completed = datetime.fromtimestamp(int(observed.timestamp()) // 900 * 900 - 900, tz=timezone.utc)
    raw_tail = tail_fetcher(symbol=symbol, interval="15m", limit=1000)["bars"]
    updated, new_count, overlap_count = merge_closed_tail(
        stored, raw_tail, completed=completed, observed_at=observed
    )
    if not new_count:
        return {
            "status": "no_change", "snapshot_id": pointer["snapshot_id"],
            "last_at": stored[-1]["timestamp"].isoformat(),
        }
    evidence = {
        "source": "binance_data_api_public_ohlcv_v2", "overlap_bars": overlap_count,
        "observed_at": observed.isoformat(), "last_at": updated[-1]["timestamp"].isoformat(),
    }
    evidence_bytes = json.dumps(evidence, sort_keys=True).encode()
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    evidence_key = (
        f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=public-tail/"
        f"symbol={symbol.lower()}/interval=15m/evidence-{evidence_sha}.json"
    )
    bundle = build_lake_bundle_v2(
        provider="binance", exchange="binance", market="spot", symbol=SYMBOLS[symbol],
        base_candles=updated,
        source_objects=[
            {"snapshot_id": pointer["snapshot_id"], "manifest_key": pointer["manifest_key"]},
            {"key": evidence_key, "sha256": evidence_sha, **evidence},
        ],
        code_version=code_version, previous_snapshot=pointer["snapshot_id"],
        allow_gaps=bool(manifest["quality"]["gaps"]),
        source_quality={
            "off_grid": manifest["quality"].get("off_grid", 0),
            "off_grid_details": manifest["quality"].get("off_grid_details", []),
        },
    )
    bundle["objects"][evidence_key] = evidence_bytes
    current_pointer = json.loads(
        s3_client.get_object(Bucket=bucket, Key=pointer_key)["Body"].read()
    )
    if current_pointer != pointer:
        raise ValueError("v2 pointer changed during incremental build")
    publish_bundle_v2(s3_client=s3_client, bucket=bucket, bundle=bundle)
    return {
        "status": "published", "snapshot_id": bundle["snapshot_id"],
        "previous_snapshot": pointer["snapshot_id"], "new_bars": new_count,
        "last_at": updated[-1]["timestamp"].isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Binance BTC candle-v2 lake")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--symbol", choices=sorted(SYMBOLS), required=True)
    args = parser.parse_args()
    result = update_once_v2(
        s3_client=boto3.client("s3", region_name=args.region),
        bucket=args.bucket, code_version=args.code_version, symbol=args.symbol,
    )
    print(json.dumps({"execution_mode": "RESEARCH_ONLY", **result}, indent=2))


if __name__ == "__main__":
    main()
