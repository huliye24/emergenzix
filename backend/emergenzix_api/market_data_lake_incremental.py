"""Idempotent closed-candle increment for an existing S3 data-lake snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import boto3
import pyarrow.parquet as pq

from .market_data_lake import ROOT_PREFIX, build_lake_bundle, publish_bundle, validate_base_bars
from .market_history import fetch_binance_public_tail


DEFAULT_BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"


def _read_base(s3_client: Any, bucket: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for item in manifest["objects"]:
        if item["timeframe"] != "15m":
            continue
        payload = s3_client.get_object(Bucket=bucket, Key=item["key"])["Body"].read()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"stored Parquet hash mismatch for {item['key']}")
        table = pq.read_table(io.BytesIO(payload))
        columns = table.to_pydict()
        for index, stamp in enumerate(columns["timestamp"]):
            bars.append({
                "timestamp": stamp, "open": columns["open"][index],
                "high": columns["high"][index], "low": columns["low"][index],
                "close": columns["close"][index], "volume": columns["volume"][index],
            })
    return validate_base_bars(
        sorted(bars, key=lambda bar: bar["timestamp"]),
        allow_gaps=bool(manifest.get("quality", {}).get("gaps", 0)),
    )


def update_once(
    *, s3_client: Any, bucket: str, symbol: str, code_version: str,
    tail_fetcher: Callable[..., dict[str, Any]] = fetch_binance_public_tail,
    now: datetime | None = None,
) -> dict[str, Any]:
    symbol_key = symbol.lower().replace("/", "")
    pointer_key = f"{ROOT_PREFIX}/pointers/binance-spot-{symbol_key}-15m-latest.json"
    pointer = json.loads(s3_client.get_object(Bucket=bucket, Key=pointer_key)["Body"].read())
    manifest = json.loads(
        s3_client.get_object(Bucket=bucket, Key=pointer["manifest_key"])["Body"].read()
    )
    bars = _read_base(s3_client, bucket, manifest)
    last_stored = bars[-1]["timestamp"]
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    completed = datetime.fromtimestamp(
        int(current.timestamp()) // 900 * 900 - 900, tz=timezone.utc
    )
    raw_tail = tail_fetcher(symbol=symbol_key.upper(), interval="15m", limit=1000)["bars"]
    tail = []
    for item in raw_tail:
        stamp = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        if stamp <= completed:
            tail.append({**item, "timestamp": stamp})
    if not tail or tail[-1]["timestamp"] <= last_stored:
        return {"status": "no_change", "snapshot_id": pointer["snapshot_id"], "last_at": last_stored.isoformat()}
    new_tail = [item for item in tail if item["timestamp"] > last_stored]
    if new_tail[0]["timestamp"] - last_stored != timedelta(minutes=15) or any(
        right["timestamp"] - left["timestamp"] != timedelta(minutes=15)
        for left, right in zip(new_tail, new_tail[1:])
    ):
        raise ValueError("incremental tail would introduce a new gap")
    stored = {bar["timestamp"]: bar for bar in bars}
    overlap = [bar for bar in tail if bar["timestamp"] in stored]
    if len(overlap) < 6:
        raise ValueError("incremental tail has insufficient overlap")
    for item in overlap:
        previous = stored[item["timestamp"]]
        if any(float(previous[key]) != float(item[key]) for key in ("open", "high", "low", "close", "volume")):
            raise ValueError(f"incremental source mismatch at {item['timestamp'].isoformat()}")
    merged = {**stored, **{item["timestamp"]: item for item in tail}}
    inherited_gaps = bool(manifest.get("quality", {}).get("gaps", 0))
    updated = validate_base_bars(
        [merged[stamp] for stamp in sorted(merged)], allow_gaps=inherited_gaps
    )
    evidence = {
        "source": "binance_data_api_public_ohlcv", "overlap_bars": len(overlap),
        "first_at": tail[0]["timestamp"].isoformat(), "last_at": tail[-1]["timestamp"].isoformat(),
    }
    evidence_bytes = json.dumps(evidence, sort_keys=True).encode()
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    evidence_key = f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=public-tail/symbol={symbol_key}/interval=15m/evidence-{evidence_sha}.json"
    bundle = build_lake_bundle(
        symbol=manifest["symbol"], base_bars=updated,
        source_objects=[
            {"snapshot_id": pointer["snapshot_id"], "manifest_key": pointer["manifest_key"]},
            {"key": evidence_key, "sha256": evidence_sha, **evidence},
        ],
        code_version=code_version, previous_snapshot=pointer["snapshot_id"],
        allow_gaps=inherited_gaps,
    )
    bundle["objects"][evidence_key] = evidence_bytes
    publish_bundle(s3_client=s3_client, bucket=bucket, bundle=bundle)
    return {
        "status": "published", "snapshot_id": bundle["snapshot_id"],
        "previous_snapshot": pointer["snapshot_id"], "last_at": updated[-1]["timestamp"].isoformat(),
        "new_bars": len(updated) - len(bars),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Update a verified closed-candle S3 snapshot")
    parser.add_argument("--symbol", default="BTCUSDT", choices=["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    result = update_once(
        s3_client=boto3.client("s3", region_name=args.region), bucket=args.bucket,
        symbol=args.symbol, code_version=args.code_version,
    )
    print(json.dumps({"execution_mode": "RESEARCH_ONLY", **result}, indent=2))


if __name__ == "__main__":
    main()
