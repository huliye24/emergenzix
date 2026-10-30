"""Import a verified Hong Kong collection bundle into the Ningxia S3 lake."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from .market_data_lake import ROOT_PREFIX, build_lake_bundle, publish_bundle, validate_base_bars
from .market_history import fetch_binance_public_tail


DEFAULT_BUCKET = "emergenzix-research-382309211832-cn-northwest-1-an"


def _archive_timestamp(value: str) -> datetime:
    raw = int(value)
    return datetime.fromtimestamp(
        raw / (1_000_000 if raw >= 100_000_000_000_000 else 1_000),
        tz=timezone.utc,
    )


def load_collection_bundle(path: Path, *, allow_gaps: bool = False) -> dict[str, Any]:
    raw_objects: dict[str, bytes] = {}
    source_objects: list[dict[str, Any]] = []
    bars_by_time: dict[datetime, dict[str, Any]] = {}
    with tarfile.open(path, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        if "manifest.json" not in members:
            raise ValueError("collection bundle has no manifest")
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
            match = re.search(rb"\b([0-9a-fA-F]{64})\b", checksum)
            digest = hashlib.sha256(payload).hexdigest()
            if match is None or match.group(1).decode().lower() != digest:
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
            zip_key, checksum_key = f"{prefix}/{filename}", f"{prefix}/{filename}.CHECKSUM"
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
                    rows = csv.reader(io.TextIOWrapper(stream, encoding="utf-8"))
                    for row in rows:
                        try:
                            stamp = _archive_timestamp(row[0])
                            bar = {
                                "timestamp": stamp, "open": float(row[1]),
                                "high": float(row[2]), "low": float(row[3]),
                                "close": float(row[4]), "volume": float(row[5]),
                            }
                        except (IndexError, ValueError, OverflowError):
                            continue
                        previous = bars_by_time.get(stamp)
                        if previous is not None and previous != bar:
                            raise ValueError(f"conflicting duplicate candle at {stamp.isoformat()}")
                        bars_by_time[stamp] = bar
    bars = [bars_by_time[stamp] for stamp in sorted(bars_by_time)]
    validate_base_bars(bars, allow_gaps=allow_gaps)
    return {
        "bars": bars, "raw_objects": raw_objects,
        "source_objects": source_objects, "collector_manifest": manifest,
    }


def merge_public_tail(archive_bars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tail = fetch_binance_public_tail(symbol="BTCUSDT", interval="15m", limit=1000)["bars"]
    archive_by_time = {bar["timestamp"]: bar for bar in archive_bars}
    normalized_tail = []
    for bar in tail:
        stamp = datetime.fromisoformat(str(bar["timestamp"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        normalized_tail.append({**bar, "timestamp": stamp})
    tail_by_time = {bar["timestamp"]: bar for bar in normalized_tail}
    overlap = sorted(set(archive_by_time) & set(tail_by_time))
    if len(overlap) < 6:
        raise ValueError("collection and public tail have insufficient overlap")
    for stamp in overlap:
        for field in ("open", "high", "low", "close", "volume"):
            if float(archive_by_time[stamp][field]) != float(tail_by_time[stamp][field]):
                raise ValueError(f"collection/public mismatch at {stamp.isoformat()}")
    now = datetime.now(timezone.utc)
    seconds = 900
    completed = datetime.fromtimestamp(
        int(now.timestamp()) // seconds * seconds - seconds, tz=timezone.utc
    )
    merged = {**archive_by_time, **{k: v for k, v in tail_by_time.items() if k <= completed}}
    bars = [merged[stamp] for stamp in sorted(merged)]
    validate_base_bars(bars)
    return bars, {"overlap_bars": len(overlap), "latest_completed_at": completed.isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a relayed BTC 15m collection into S3")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default="cn-northwest-1")
    parser.add_argument("--code-version", required=True)
    args = parser.parse_args()
    loaded = load_collection_bundle(args.bundle)
    bars, tail_evidence = merge_public_tail(loaded["bars"])
    tail_payload = json.dumps(tail_evidence, sort_keys=True).encode()
    tail_digest = hashlib.sha256(tail_payload).hexdigest()
    tail_key = f"{ROOT_PREFIX}/bronze/provider=binance/market=spot/kind=public-tail/symbol=btcusdt/interval=15m/evidence-{tail_digest}.json"
    loaded["raw_objects"][tail_key] = tail_payload
    loaded["source_objects"].append({"key": tail_key, "sha256": tail_digest, **tail_evidence})
    bundle = build_lake_bundle(
        symbol="BTC/USDT", base_bars=bars,
        source_objects=loaded["source_objects"], code_version=args.code_version,
    )
    bundle["objects"].update(loaded["raw_objects"])
    publish_bundle(
        s3_client=boto3.client("s3", region_name=args.region),
        bucket=args.bucket, bundle=bundle,
    )
    print(json.dumps({
        "execution_mode": "RESEARCH_ONLY", "symbol": "BTCUSDT",
        "snapshot_id": bundle["snapshot_id"], "manifest_key": bundle["manifest_key"],
        "base_rows": len(bars), "first_at": bars[0]["timestamp"].isoformat(),
        "last_at": bars[-1]["timestamp"].isoformat(), "objects": len(bundle["objects"]),
        **tail_evidence,
    }, indent=2))


if __name__ == "__main__":
    main()
