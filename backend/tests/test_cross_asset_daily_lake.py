from datetime import UTC, date, datetime, timedelta
import hashlib
import json

import pytest

from emergenzix_api.cross_asset_daily_lake import (
    INSTRUMENTS,
    build_provider_bundle,
    export_bundle,
    compare_provider_returns,
    comparison_bundle,
    parse_dukascopy_json,
    load_catalog,
    DEFAULT_CATALOG,
    build_catalog_snapshot,
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


def test_parse_dukascopy_and_compare_without_merging():
    raw = json.dumps([
        {"timestamp": 1767312000000, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
        {"timestamp": 1767398400000, "open": 101, "high": 104, "low": 100, "close": 103, "volume": 12},
        {"timestamp": 1767484800000, "open": 103, "high": 104, "low": 101, "close": 102, "volume": 11},
    ]).encode()
    secondary = parse_dukascopy_json(raw)
    primary = _bars()
    primary.append({**_bars()[-1], "timestamp": datetime(2026, 1, 4, tzinfo=UTC),
                    "available_at": datetime(2026, 1, 5, tzinfo=UTC), "close": 102.0})
    left_bundle = {"snapshot_id": "a" * 64}
    right_bundle = {"snapshot_id": "b" * 64}
    report = compare_provider_returns(
        instrument_id="GC", primary_provider="one", primary_bundle=left_bundle,
        primary_bars=primary, secondary_provider="two", secondary_bundle=right_bundle,
        secondary_bars=secondary,
    )
    assert report["merge_or_imputation_performed"] is False
    assert report["common_return_days"] == 2
    assert -1 <= report["pearson_return_correlation"] <= 1
    bundled = comparison_bundle(report)
    assert "/gold/comparisons/instrument=gc/" in bundled["manifest_key"]


def test_versioned_catalog_has_exactly_100_unique_research_instruments():
    catalog_id, instruments = load_catalog(DEFAULT_CATALOG)
    assert catalog_id == "cross-asset-100-v2"
    assert len(instruments) == 100
    assert len({item.instrument_id for item in instruments}) == 100
    assert {item.asset_class for item in instruments} == {
        "equity", "etf", "index", "commodity", "fx"
    }
    assert {"EURCHF", "USDSEK"} <= {item.instrument_id for item in instruments}
    assert not {"USDINR", "USDBRL"} & {item.instrument_id for item in instruments}


def test_history_coverage_gate_rejects_short_vendor_response():
    with pytest.raises(ValueError, match="history coverage gate failed"):
        build_provider_bundle(
            provider="example", instrument=INSTRUMENTS[0], raw=b"{}", bars=_bars(),
            start=date(2021, 1, 1), end=date(2026, 1, 1), code_version="test",
            minimum_rows=900,
        )


def test_catalog_snapshot_fails_closed_on_uncovered_instrument():
    catalog_id, instruments = load_catalog(DEFAULT_CATALOG)
    sample = build_provider_bundle(
        provider="example", instrument=instruments[0], raw=b"{}", bars=_bars(),
        start=date(2026, 1, 1), end=date(2026, 1, 31), code_version="abc",
    )["manifest"]
    bundle = build_catalog_snapshot(
        catalog_id=catalog_id, instruments=instruments,
        provider_manifests=[sample], code_version="abc",
    )
    assert bundle["manifest"]["covered_instruments"] == 1
    assert bundle["manifest"]["uncovered_instruments"] == 99
    assert bundle["manifest"]["cross_provider_imputation"] is False
