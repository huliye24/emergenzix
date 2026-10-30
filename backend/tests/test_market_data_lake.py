from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError

from emergenzix_api.market_data_lake import (
    build_lake_bundle,
    publish_bundle,
    resample_bars,
    validate_base_bars,
)


UTC = timezone.utc


def bars(days: int = 40):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        {
            "timestamp": start + timedelta(minutes=15 * index),
            "open": 100 + index, "high": 102 + index, "low": 99 + index,
            "close": 101 + index, "volume": 10 + index,
        }
        for index in range(days * 96)
    ]


def test_resamples_all_boundaries_and_drops_incomplete_buckets() -> None:
    source = bars(40)[:-1]
    assert len(resample_bars(source, "1h")) == (len(source) // 4)
    assert len(resample_bars(source, "4h")) == (len(source) // 16)
    assert len(resample_bars(source, "1d")) == 39
    assert resample_bars(source, "1w")[0]["timestamp"].weekday() == 0
    monthly = resample_bars(source, "1mo")
    assert len(monthly) == 1
    assert monthly[0]["timestamp"] == datetime(2024, 1, 1, tzinfo=UTC)


def test_base_validation_rejects_gap_or_off_grid() -> None:
    source = bars(1)
    with pytest.raises(ValueError, match="gap-free"):
        validate_base_bars(source[:10] + source[11:])
    source[3]["timestamp"] += timedelta(minutes=1)
    with pytest.raises(ValueError, match="off the UTC"):
        validate_base_bars(source)


def test_gap_aware_bundle_records_missing_bars_and_omits_incomplete_gold() -> None:
    source = bars(2)
    del source[10]
    bundle = build_lake_bundle(
        symbol="BTC/USDT", base_bars=source,
        source_objects=[{"key": "bronze/gapped.zip", "sha256": "c" * 64}],
        code_version="test-commit", allow_gaps=True,
    )
    assert bundle["manifest"]["quality"]["gaps"] == 1
    assert bundle["manifest"]["quality"]["missing_candles"] == 1
    one_hour_rows = sum(
        item["rows"] for item in bundle["manifest"]["objects"]
        if item["timeframe"] == "1h"
    )
    assert one_hour_rows == 47


def test_bundle_is_deterministic_and_parquet_is_readable(tmp_path) -> None:
    kwargs = {
        "symbol": "BTC/USDT", "base_bars": bars(40),
        "source_objects": [{"key": "bronze/example.zip", "sha256": "a" * 64}],
        "code_version": "test-commit",
    }
    first = build_lake_bundle(**kwargs)
    second = build_lake_bundle(**kwargs)
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["objects"] == second["objects"]
    parquet_key = next(key for key in first["objects"] if key.endswith(".parquet"))
    path = tmp_path / "part.parquet"
    path.write_bytes(first["objects"][parquet_key])
    table = pq.read_table(path)
    assert table.num_rows > 0
    assert table.schema.metadata[b"schema_version"] == b"emergenzix-market-candle-v1"
    assert first["manifest"]["quality"]["gaps"] == 0


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)

    def head_object(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "404", "Message": "missing"},
             "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )


def test_publisher_uses_only_market_data_prefix_and_encryption() -> None:
    bundle = build_lake_bundle(
        symbol="ETH/USDT", base_bars=bars(2),
        source_objects=[{"key": "bronze/eth.zip", "sha256": "b" * 64}],
        code_version="test-commit",
    )
    client = FakeS3()
    publish_bundle(s3_client=client, bucket="research-bucket", bundle=bundle)
    assert client.calls
    assert all(call["Key"].startswith("market-data/v1/") for call in client.calls)
    assert all(call["ServerSideEncryption"] == "AES256" for call in client.calls)
    assert any("pointers/binance-spot-ethusdt-15m-latest.json" in call["Key"] for call in client.calls)
