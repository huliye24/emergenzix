from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from emergenzix_api.eami_g1_features import build_features


def test_builds_complete_4h_return_features_deterministically(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    timestamps = [start + timedelta(minutes=15 * index) for index in range(32)]
    pq.write_table(
        pa.table(
            {
                "timestamp": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
                "close": [100.0 + index for index in range(32)],
                "is_closed": [True] * 32,
                "is_imputed": [False] * 32,
            }
        ),
        source / "btcusdt-2026-11.parquet",
    )
    output = tmp_path / "g1.parquet"
    first = build_features(input_root=source, output=output)
    second = build_features(input_root=source, output=output)
    assert first["content_sha256"] == second["content_sha256"]
    assert first["rows"] == 2
    rows = pq.read_table(output).to_pylist()
    assert rows[0]["return_1"] is None
    assert rows[1]["return_1"] == (131.0 / 115.0) - 1.0
