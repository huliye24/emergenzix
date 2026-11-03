from __future__ import annotations

import hashlib
import json
from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from emergenzix_api.research_snapshot_set import (
    build_snapshot_set,
    publish_snapshot_set,
)


def _manifest(symbol: str, snapshot_seed: str) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": "emergenzix-market-candle-v2",
        "provider": "binance",
        "exchange": "binance",
        "market": "spot",
        "symbol": symbol,
        "base_timeframe": "15m",
        "first_at": "2025-01-01T00:00:00+00:00",
        "last_at": "2026-12-31T23:45:00+00:00",
        "base_rows": 1_000,
        "objects": [],
        "source_objects": [],
        "code_version": snapshot_seed,
        "quality": {
            "gaps": 0,
            "missing_candles": 0,
            "forming_candle_excluded": True,
            "imputed_rows": 0,
        },
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    manifest["snapshot_id"] = hashlib.sha256(canonical).hexdigest()
    return manifest


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict[str, object]] = []

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
        if Key not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        return {"Body": BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])
        self.puts.append(kwargs)


def test_snapshot_set_is_deterministic_and_requires_three_symbols() -> None:
    manifests = [
        _manifest("ETH/USDT", "eth"),
        _manifest("BTC/USDT", "btc"),
        _manifest("BNB/USDT", "bnb"),
    ]
    kwargs = {
        "manifests": manifests,
        "selected_start": "2025-11-01T00:00:00Z",
        "selected_end_exclusive": "2026-11-02T00:00:00Z",
        "local_mirror_sha256": "a" * 64,
    }
    first = build_snapshot_set(**kwargs)
    second = build_snapshot_set(**kwargs)

    assert first["collection_id"] == second["collection_id"]
    assert [item["symbol"] for item in first["manifest"]["members"]] == [
        "BNB/USDT",
        "BTC/USDT",
        "ETH/USDT",
    ]
    with pytest.raises(ValueError, match="exactly BTC/ETH/BNB"):
        build_snapshot_set(**{**kwargs, "manifests": manifests[:2]})


def test_publish_is_idempotent_and_never_writes_pointer() -> None:
    bundle = build_snapshot_set(
        manifests=[
            _manifest("BTC/USDT", "btc"),
            _manifest("ETH/USDT", "eth"),
            _manifest("BNB/USDT", "bnb"),
        ],
        selected_start="2025-11-01T00:00:00Z",
        selected_end_exclusive="2026-11-02T00:00:00Z",
        local_mirror_sha256="b" * 64,
    )
    s3 = FakeS3()
    publish_snapshot_set(s3_client=s3, bucket="research", bundle=bundle)
    publish_snapshot_set(s3_client=s3, bucket="research", bundle=bundle)

    assert len(s3.puts) == 1
    assert s3.puts[0]["ServerSideEncryption"] == "AES256"
    assert "/collections/snapshot=" in str(s3.puts[0]["Key"])
    assert "/pointers/" not in str(s3.puts[0]["Key"])

    s3.objects[str(bundle["manifest_key"])] = b"tampered"
    with pytest.raises(ValueError, match="content mismatch"):
        publish_snapshot_set(s3_client=s3, bucket="research", bundle=bundle)
