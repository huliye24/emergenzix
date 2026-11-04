"""Build the first reproducible BTCUSDT 4h return feature asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def build_features(*, input_root: Path, output: Path) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    paths = sorted(input_root.glob("btcusdt-*.parquet"), key=lambda item: item.name)
    if not paths:
        raise ValueError("no BTCUSDT Parquet inputs found")
    rows = []
    source_hashes = []
    for path in paths:
        source_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        rows.extend(
            pq.read_table(
                path, columns=["timestamp", "close", "is_closed", "is_imputed"]
            ).to_pylist()
        )
    rows = sorted(
        (row for row in rows if row["is_closed"] and not row["is_imputed"]),
        key=lambda row: row["timestamp"],
    )
    groups: dict[datetime, list[dict]] = {}
    for row in rows:
        stamp = row["timestamp"].astimezone(timezone.utc)
        bucket = stamp.replace(
            hour=(stamp.hour // 4) * 4, minute=0, second=0, microsecond=0
        )
        groups.setdefault(bucket, []).append(row)
    completed = []
    for bucket, members in sorted(groups.items()):
        expected = [bucket + timedelta(minutes=15 * index) for index in range(16)]
        observed = [item["timestamp"].astimezone(timezone.utc) for item in members]
        if observed == expected:
            completed.append((bucket, float(members[-1]["close"])))
    if len(completed) < 2:
        raise ValueError("at least two complete 4h candles are required")
    timestamps = [item[0] for item in completed]
    closes = [item[1] for item in completed]
    returns = [None] + [
        closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))
    ]
    table = pa.table(
        {
            "timestamp": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            "close": pa.array(closes, type=pa.float64()),
            "return_1": pa.array(returns, type=pa.float64()),
        }
    ).replace_schema_metadata(
        {
            b"schema_version": b"eami-g1-return-feature-v1",
            b"feature_version": b"g1-return-v1",
            b"symbol": b"BTCUSDT",
            b"timeframe": b"4h",
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    pq.write_table(table, temporary, compression="zstd", version="2.6")
    temporary.replace(output)
    result = {
        "output": str(output.resolve()),
        "content_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_file_count": len(paths),
        "source_sha256": source_hashes,
        "rows": len(completed),
        "first_at": timestamps[0].isoformat(),
        "last_at": timestamps[-1].isoformat(),
        "feature_version": "g1-return-v1",
        "order_permission": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_features(input_root=args.input_root, output=args.output),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
