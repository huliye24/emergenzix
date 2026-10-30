from datetime import UTC, date, datetime, timedelta
import hashlib
import json

import pytest

from emergenzix_api.cross_asset_daily_lake import (
    INSTRUMENTS,
    build_provider_bundle,
    export_bundle,
    publish_bundle,
    validate_daily_bars,
)


def _bars():
    return [
        {
            "timestamp": datetime(2026, 1, day, tzinfo=UTC),
            "available_at": datetime(2026, 1, day, tzinfo=UTC) + timedelta(days=1),
            "open": 100.0 + day,
            "high": 102.0 + day,
            "low": 99.0 + day,
            "close": 101.0 + day,
            "adjusted_close": 100.5 + day,
            "volume": 1000.0,
            "is_imputed": False,
        }
        for day in (2, 3)
    ]


def test_bundle_is_provider_preserving_research_only_and_content_addressed():
    raw = b'{"vendor":"evidence"}'
    bundle = build_provider_bundle(
        provider="example", instrument=INSTRUMENTS[0], raw=raw, bars=_bars(),
        start=date(2026, 1, 1), end=date(2026, 1, 31), code_version="test",
    )
    manifest = bundle["manifest"]
    assert manifest["research_only"] is True
    assert manifest["promotion_permission"] == "none"
    assert manifest["cross_provider_imputation"] is False
    assert manifest["quality"]["rows"] == 2
    assert any("provider=example" in item["key"] for item in manifest["objects"])
    canonical = {key: value for key, value in manifest.items() if key != "snapshot_id"}
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert bundle["snapshot_id"] == expected


def test_quality_gate_rejects_invalid_ohlc_and_imputation():
    invalid = _bars()
    invalid[0]["high"] = 50.0
    with pytest.raises(ValueError, match="quality gate failed"):
        validate_daily_bars(invalid)
    imputed = _bars()
    imputed[0]["is_imputed"] = True
    with pytest.raises(ValueError, match="cannot be imputed"):
        validate_daily_bars(imputed)


def test_publish_marks_every_object_encrypted_and_research_only():
    class FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)

    bundle = build_provider_bundle(
        provider="example", instrument=INSTRUMENTS[1], raw=b"{}", bars=_bars(),
        start=date(2026, 1, 1), end=date(2026, 1, 31), code_version="test",
    )
    s3 = FakeS3()
    publish_bundle(s3_client=s3, bucket="test-bucket", bundle=bundle)
    assert len(s3.calls) == 3
    assert all(call["ServerSideEncryption"] == "AES256" for call in s3.calls)
    assert all(call["Metadata"]["research-only"] == "true" for call in s3.calls)


def test_export_preserves_exact_s3_object_keys(tmp_path):
    bundle = build_provider_bundle(
        provider="example", instrument=INSTRUMENTS[0], raw=b"{}", bars=_bars(),
        start=date(2026, 1, 1), end=date(2026, 1, 31), code_version="test",
    )
    export_bundle(root=tmp_path, bundle=bundle)
    for key, payload in bundle["objects"].items():
        assert tmp_path.joinpath(*key.split("/")).read_bytes() == payload
