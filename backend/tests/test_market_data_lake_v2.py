import io
import json
from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from emergenzix_api.market_data_lake_v2 import build_lake_bundle_v2, publish_bundle_v2


UTC = timezone.utc


def candles(days: int = 2):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for index in range(days * 96):
        opened = start + timedelta(minutes=15 * index)
        result.append({
            "timestamp": opened,
            "close_time": opened + timedelta(minutes=15),
            "available_at": opened + timedelta(minutes=15),
            "open": 100 + index, "high": 102 + index,
            "low": 99 + index, "close": 101 + index,
            "base_volume": 10 + index, "quote_volume": 1000 + index,
            "trade_count": 20 + index,
            "taker_buy_base_volume": 4 + index,
            "taker_buy_quote_volume": 400 + index,
            "is_closed": True, "is_imputed": False,
        })
    return result


def test_v2_bundle_preserves_volume_schema_and_is_deterministic() -> None:
    kwargs = {
        "provider": "binance", "exchange": "binance", "market": "spot",
        "symbol": "BTC/USDT", "base_candles": candles(),
        "source_objects": [{"key": "bronze/source.zip", "sha256": "a" * 64}],
        "code_version": "test-commit",
    }
    first = build_lake_bundle_v2(**kwargs)
    second = build_lake_bundle_v2(**kwargs)
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["manifest"]["field_non_null_rows"]["quote_volume"] == 192
    key = next(
        item["key"] for item in first["manifest"]["objects"]
        if item["timeframe"] == "15m"
    )
    table = pq.read_table(io.BytesIO(first["objects"][key]))
    assert table.schema.metadata[b"schema_version"] == b"emergenzix-market-candle-v2"
    assert table.column("quote_volume").null_count == 0
    assert table.column("trade_count").type == "int64"


class FakeS3:
    def __init__(self):
        self.calls = []

    def head_object(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_v2_publisher_never_advances_a_v1_pointer() -> None:
    bundle = build_lake_bundle_v2(
        provider="binance", exchange="binance", market="spot", symbol="BTC/USDT",
        base_candles=candles(), source_objects=[], code_version="test-commit",
    )
    client = FakeS3()
    publish_bundle_v2(s3_client=client, bucket="research", bundle=bundle)
    keys = [call["Key"] for call in client.calls]
    assert all(key.startswith("market-data/v2/") for key in keys)
    pointer = next(call for call in client.calls if "/pointers/" in call["Key"])
    assert json.loads(pointer["Body"])["snapshot_id"] == bundle["snapshot_id"]
