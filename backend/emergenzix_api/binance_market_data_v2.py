"""Build and publish Binance candle-v2 history from verified relay bundles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3

from .market_data_lake_v2 import ROOT_PREFIX, build_lake_bundle_v2, publish_bundle_v2
from .market_history import fetch_binance_public_tail


DEFAULT_BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"


def _archive_timestamp(value: str) -> datetime:
    raw = int(value)
    return datetime.fromtimestamp(
        raw / (1_000_000 if raw >= 100_000_000_000_000 else 1_000),
        tz=timezone.utc,
    )


def load_binance_bundles(paths: list[Path]) -> dict[str, Any]:
    candles_by_time: dict[datetime, dict[str, Any]] = {}
    raw_objects: dict[str, bytes] = {}
    source_objects = []
    for path in paths:
        with tarfile.open(path, "r:gz") as archive:
            members = {item.name: item for item in archive.getmembers() if item.isfile()}
            manifest = json.loads(archive.extractfile(members["manifest.json"]).read())
            if manifest.get("symbol") != "BTCUSDT" or manifest.get("interval") != "15m":
                raise ValueError("collection bundle identity does not match BTCUSDT 15m")
            for name, member in sorted(members.items()):
                if not name.endswith(".zip"):
                    continue
                checksum_name = f"{name}.CHECKSUM"
                if checksum_name not in members:
                    raise ValueError(f"missing official checksum for {name}")
                payload = archive.extractfile(member).read()
                checksum = archive.extractfile(members[checksum_name]).read()
                expected = re.search(rb"\b([0-9a-fA-F]{64})\b", checksum)
                digest = hashlib.sha256(payload).hexdigest()
                if expected is None or expected.group(1).decode().lower() != digest:
                    raise ValueError(f"checksum mismatch for {name}")
                filename = name.rsplit("/", 1)[-1]
                date_match = re.search(r"-(\d{4})-(\d{2})(?:-\d{2})?\.zip$", filename)
                if date_match is None:
                    raise ValueError(f"invalid archive filename {filename}")
                year, month = date_match.groups()
                prefix = (
                    f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=klines/"
                    f"symbol=btcusdt/interval=15m/year={year}/month={month}/sha256={digest}"
                )
                zip_key = f"{prefix}/{filename}"
                checksum_key = f"{zip_key}.CHECKSUM"
                raw_objects[zip_key], raw_objects[checksum_key] = payload, checksum
                source_objects.append({
                    "key": zip_key, "checksum_key": checksum_key,
                    "sha256": digest, "bytes": len(payload),
                })
                with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
                    csv_names = [item for item in zipped.namelist() if item.endswith(".csv")]
                    if len(csv_names) != 1:
                        raise ValueError(f"invalid CSV count in {filename}")
                    with zipped.open(csv_names[0]) as stream:
                        for row in csv.reader(io.TextIOWrapper(stream, encoding="utf-8")):
                            try:
                                opened = _archive_timestamp(row[0])
                                candle = {
                                    "timestamp": opened,
                                    "close_time": opened + timedelta(minutes=15),
                                    "available_at": opened + timedelta(minutes=15),
                                    "open": float(row[1]), "high": float(row[2]),
                                    "low": float(row[3]), "close": float(row[4]),
                                    "base_volume": float(row[5]),
                                    "quote_volume": float(row[7]), "trade_count": int(row[8]),
                                    "taker_buy_base_volume": float(row[9]),
                                    "taker_buy_quote_volume": float(row[10]),
                                    "is_closed": True, "is_imputed": False,
                                }
                            except (IndexError, ValueError, OverflowError):
                                continue
                            previous = candles_by_time.get(opened)
                            if previous is not None and previous != candle:
                                raise ValueError(f"conflicting candle at {opened.isoformat()}")
                            candles_by_time[opened] = candle
    return {
        "candles": [candles_by_time[stamp] for stamp in sorted(candles_by_time)],
        "raw_objects": raw_objects, "source_objects": source_objects,
    }


def merge_public_tail(candles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tail = fetch_binance_public_tail(symbol="BTCUSDT", interval="15m", limit=1000)["bars"]
    current = datetime.now(timezone.utc)
    completed = datetime.fromtimestamp(int(current.timestamp()) // 900 * 900 - 900, tz=timezone.utc)
    archive_by_time = {item["timestamp"]: item for item in candles}
    tail_by_time = {}
    for item in tail:
        opened = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if opened > completed:
            continue
        tail_by_time[opened] = {
            "timestamp": opened, "close_time": opened + timedelta(minutes=15),
            "available_at": opened + timedelta(minutes=15),
            "open": item["open"], "high": item["high"], "low": item["low"],
            "close": item["close"], "base_volume": item["base_volume"],
            "quote_volume": item["quote_volume"], "trade_count": item["trade_count"],
            "taker_buy_base_volume": item["taker_buy_base_volume"],
            "taker_buy_quote_volume": item["taker_buy_quote_volume"],
            "is_closed": True, "is_imputed": False,
        }
    overlap = sorted(set(archive_by_time) & set(tail_by_time))
    if len(overlap) < 6:
        raise ValueError("archive and public tail have insufficient overlap")
    fields = (
        "open", "high", "low", "close", "base_volume", "quote_volume",
        "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
    )
    for stamp in overlap:
        if any(archive_by_time[stamp][field] != tail_by_time[stamp][field] for field in fields):
            raise ValueError(f"archive/public mismatch at {stamp.isoformat()}")
    merged = {**archive_by_time, **tail_by_time}
    return [merged[stamp] for stamp in sorted(merged)], {
        "overlap_bars": len(overlap), "latest_completed_at": completed.isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Binance BTCUSDT candle-v2 history")
    parser.add_argument("--bundle", type=Path, action="append", required=True)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    loaded = load_binance_bundles(args.bundle)
    candles, tail_evidence = merge_public_tail(loaded["candles"])
    evidence = json.dumps(tail_evidence, sort_keys=True).encode()
    digest = hashlib.sha256(evidence).hexdigest()
    evidence_key = f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=public-tail/symbol=btcusdt/interval=15m/evidence-{digest}.json"
    loaded["raw_objects"][evidence_key] = evidence
    loaded["source_objects"].append({"key": evidence_key, "sha256": digest, **tail_evidence})
    bundle = build_lake_bundle_v2(
        provider="binance", exchange="binance", market="spot", symbol="BTC/USDT",
        base_candles=candles, source_objects=loaded["source_objects"],
        code_version=args.code_version, allow_gaps=True,
    )
    bundle["objects"].update(loaded["raw_objects"])
    publish_bundle_v2(
        s3_client=boto3.client("s3", region_name=args.region),
        bucket=args.bucket, bundle=bundle,
    )
    print(json.dumps({
        "execution_mode": "RESEARCH_ONLY", "status": "published",
        "snapshot_id": bundle["snapshot_id"], "base_rows": len(candles),
        "first_at": candles[0]["timestamp"].isoformat(),
        "last_at": candles[-1]["timestamp"].isoformat(),
        "field_non_null_rows": bundle["manifest"]["field_non_null_rows"],
        "quality": bundle["manifest"]["quality"], **tail_evidence,
    }, indent=2))


if __name__ == "__main__":
    main()
