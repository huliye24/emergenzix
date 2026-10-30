"""Research-only cross-asset daily data-lake pilot.

The pilot preserves each provider independently. It never fills one provider's
missing bar with another provider and grants no strategy or order permission.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Mapping, Sequence


ROOT_PREFIX = "market-data/v3"
SCHEMA_VERSION = "cross-asset-daily-v1"
DEFAULT_START = date(2021, 10, 30)
DEFAULT_END = date(2026, 10, 30)


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    asset_class: str
    vendor_symbol: str
    price_currency: str
    instrument_type: str


INSTRUMENTS = (
    Instrument("SPY", "equity", "SPY", "USD", "etf"),
    Instrument("AAPL", "equity", "AAPL", "USD", "common_stock"),
    Instrument("GC", "commodity", "GC=F", "USD", "continuous_futures_proxy"),
    Instrument("CL", "commodity", "CL=F", "USD", "continuous_futures_proxy"),
    Instrument("EURUSD", "fx", "EURUSD=X", "USD", "spot_fx"),
)

DUKASCOPY_INSTRUMENTS = (
    Instrument("EURUSD", "fx", "eurusd", "USD", "spot_fx_bid"),
    Instrument("GC", "commodity", "xauusd", "USD", "spot_gold_bid"),
    Instrument("CL", "commodity", "lightcmdusd", "USD", "commodity_cfd_bid"),
)


class ProviderError(RuntimeError):
    """Sanitized provider failure suitable for operator output."""


def _utc_epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def fetch_yahoo_chart(
    instrument: Instrument, *, start: date, end: date, timeout: float = 30.0
) -> tuple[bytes, list[dict[str, Any]]]:
    """Fetch an unadjusted daily Yahoo chart response and normalized bars.

    ``end`` is inclusive for callers; Yahoo's period2 is exclusive.
    """
    query = urllib.parse.urlencode({
        "period1": _utc_epoch(start),
        "period2": _utc_epoch(end + timedelta(days=1)),
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true",
    })
    symbol = urllib.parse.quote(instrument.vendor_symbol, safe="")
    request = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}",
        headers={"User-Agent": "Emergenzix-Research/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:
        raise ProviderError(f"yahoo request failed ({type(exc).__name__})") from exc
    try:
        result = json.loads(raw)["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProviderError("yahoo response schema was not recognized") from exc

    bars = []
    fields = ("open", "high", "low", "close", "volume")
    for index, epoch in enumerate(timestamps):
        values = {field: quote.get(field, [None] * len(timestamps))[index] for field in fields}
        if any(values[field] is None for field in ("open", "high", "low", "close")):
            continue
        stamp = datetime.fromtimestamp(epoch, tz=UTC)
        bars.append({
            "timestamp": stamp,
            "available_at": stamp + timedelta(days=1),
            "open": float(values["open"]),
            "high": float(values["high"]),
            "low": float(values["low"]),
            "close": float(values["close"]),
            "adjusted_close": (
                float(adjusted[index]) if index < len(adjusted) and adjusted[index] is not None else None
            ),
            "volume": float(values["volume"]) if values["volume"] is not None else None,
            "is_imputed": False,
        })
    return raw, bars


def parse_dukascopy_json(raw: bytes) -> list[dict[str, Any]]:
    """Normalize dukascopy-node d1 JSON; the raw bytes remain Bronze evidence."""
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError("dukascopy JSON was not valid") from exc
    if not isinstance(records, list):
        raise ProviderError("dukascopy JSON root must be an array")
    bars = []
    try:
        for item in records:
            stamp = datetime.fromtimestamp(int(item["timestamp"]) / 1000, tz=UTC)
            bars.append({
                "timestamp": stamp,
                "available_at": stamp + timedelta(days=1),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "adjusted_close": None,
                "volume": float(item["volume"]) if item.get("volume") is not None else None,
                "is_imputed": False,
            })
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProviderError("dukascopy record schema was not recognized") from exc
    return bars


def validate_daily_bars(bars: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized = sorted((dict(item) for item in bars), key=lambda item: item["timestamp"])
    if not normalized:
        raise ValueError("provider returned no usable daily bars")
    duplicate_count = len(normalized) - len({item["timestamp"] for item in normalized})
    invalid_ohlc = 0
    negative_volume = 0
    for item in normalized:
        if item["timestamp"].tzinfo is None:
            raise ValueError("daily timestamps must be timezone-aware")
        if not (
            item["low"] <= item["open"] <= item["high"]
            and item["low"] <= item["close"] <= item["high"]
        ):
            invalid_ohlc += 1
        if item["volume"] is not None and item["volume"] < 0:
            negative_volume += 1
        if item.get("is_imputed"):
            raise ValueError("canonical provider bars cannot be imputed")
    if duplicate_count or invalid_ohlc or negative_volume:
        raise ValueError(
            f"quality gate failed: duplicates={duplicate_count}, "
            f"invalid_ohlc={invalid_ohlc}, negative_volume={negative_volume}"
        )
    gaps = [
        {"after": left["timestamp"].date().isoformat(), "before": right["timestamp"].date().isoformat(),
         "calendar_days": (right["timestamp"].date() - left["timestamp"].date()).days}
        for left, right in zip(normalized, normalized[1:])
        if (right["timestamp"].date() - left["timestamp"].date()).days > 4
    ]
    return normalized, {
        "rows": len(normalized),
        "duplicates": duplicate_count,
        "invalid_ohlc": invalid_ohlc,
        "negative_volume": negative_volume,
        "long_calendar_gaps": len(gaps),
        "long_calendar_gap_details": gaps,
        "volume_non_null_rows": sum(item["volume"] is not None for item in normalized),
        "adjusted_close_non_null_rows": sum(item["adjusted_close"] is not None for item in normalized),
    }


def _parquet_bytes(bars: Sequence[Mapping[str, Any]], metadata: Mapping[str, str]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    def timestamps(field: str):
        return pa.array([item[field] for item in bars], type=pa.timestamp("us", tz="UTC"))

    table = pa.table({
        "timestamp": timestamps("timestamp"),
        "available_at": timestamps("available_at"),
        **{field: pa.array([item[field] for item in bars], type=pa.float64())
           for field in ("open", "high", "low", "close", "adjusted_close", "volume")},
        "is_imputed": pa.array([False] * len(bars), type=pa.bool_()),
    }).replace_schema_metadata({key.encode(): value.encode() for key, value in metadata.items()})
    stream = io.BytesIO()
    pq.write_table(table, stream, compression="zstd", version="2.6")
    return stream.getvalue()


def build_provider_bundle(
    *, provider: str, instrument: Instrument, raw: bytes,
    bars: Sequence[Mapping[str, Any]], start: date, end: date, code_version: str,
) -> dict[str, Any]:
    normalized, quality = validate_daily_bars(bars)
    raw_sha = hashlib.sha256(raw).hexdigest()
    parquet = _parquet_bytes(normalized, {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "instrument_id": instrument.instrument_id,
        "vendor_symbol": instrument.vendor_symbol,
        "asset_class": instrument.asset_class,
        "timeframe": "1d",
        "research_only": "true",
    })
    parquet_sha = hashlib.sha256(parquet).hexdigest()
    slug = instrument.instrument_id.lower()
    raw_key = (
        f"{ROOT_PREFIX}/bronze/provider={provider}/asset_class={instrument.asset_class}/"
        f"instrument={slug}/timeframe=1d/raw-{raw_sha}.json"
    )
    silver_key = (
        f"{ROOT_PREFIX}/silver/provider={provider}/asset_class={instrument.asset_class}/"
        f"instrument={slug}/timeframe=1d/part-{parquet_sha}.parquet"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "instrument_id": instrument.instrument_id,
        "vendor_symbol": instrument.vendor_symbol,
        "asset_class": instrument.asset_class,
        "instrument_type": instrument.instrument_type,
        "price_currency": instrument.price_currency,
        "timeframe": "1d",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "first_at": normalized[0]["timestamp"].isoformat(),
        "last_at": normalized[-1]["timestamp"].isoformat(),
        "research_only": True,
        "promotion_permission": "none",
        "cross_provider_imputation": False,
        "adjustment_policy": "raw_ohlc_with_vendor_adjusted_close_separate",
        "availability_policy": "conservative_next_utc_day",
        "quality": quality,
        "objects": [
            {"zone": "bronze", "key": raw_key, "sha256": raw_sha, "bytes": len(raw)},
            {"zone": "silver", "key": silver_key, "sha256": parquet_sha,
             "bytes": len(parquet), "rows": len(normalized)},
        ],
        "code_version": code_version,
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    snapshot_id = hashlib.sha256(canonical).hexdigest()
    manifest = {**manifest, "snapshot_id": snapshot_id}
    manifest_key = f"{ROOT_PREFIX}/manifests/snapshot={snapshot_id}/manifest.json"
    return {
        "snapshot_id": snapshot_id,
        "manifest_key": manifest_key,
        "manifest": manifest,
        "objects": {
            raw_key: raw,
            silver_key: parquet,
            manifest_key: json.dumps(manifest, sort_keys=True, indent=2).encode(),
        },
    }


def publish_bundle(*, s3_client: Any, bucket: str, bundle: Mapping[str, Any]) -> None:
    for key, payload in bundle["objects"].items():
        digest = hashlib.sha256(payload).hexdigest()
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType=("application/json" if key.endswith(".json") else "application/vnd.apache.parquet"),
            Metadata={"sha256": digest, "research-only": "true"},
            ServerSideEncryption="AES256",
        )


def export_bundle(*, root: Path, bundle: Mapping[str, Any]) -> None:
    """Write content-addressed objects below ``root`` using their exact S3 keys."""
    for key, payload in bundle["objects"].items():
        target = root.joinpath(*key.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def compare_provider_returns(
    *, instrument_id: str, primary_provider: str, primary_bundle: Mapping[str, Any],
    primary_bars: Sequence[Mapping[str, Any]], secondary_provider: str,
    secondary_bundle: Mapping[str, Any], secondary_bars: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare same-date close returns without treating either source as a repair."""
    def returns(bars: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        ordered = sorted(bars, key=lambda item: item["timestamp"])
        return {
            current["timestamp"].date().isoformat(): math.log(current["close"] / previous["close"])
            for previous, current in zip(ordered, ordered[1:])
            if previous["close"] > 0 and current["close"] > 0
        }

    left = returns(primary_bars)
    right = returns(secondary_bars)
    common = sorted(set(left) & set(right))
    if len(common) < 2:
        raise ValueError("provider comparison requires at least two common returns")
    x = [left[day] for day in common]
    y = [right[day] for day in common]
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - x_mean) ** 2 for a in x) * sum((b - y_mean) ** 2 for b in y)
    )
    differences = sorted(abs(a - b) for a, b in zip(x, y))
    midpoint = len(differences) // 2
    median_difference = (
        differences[midpoint] if len(differences) % 2
        else (differences[midpoint - 1] + differences[midpoint]) / 2
    )
    return {
        "schema_version": "cross-provider-return-comparison-v1",
        "instrument_id": instrument_id,
        "research_only": True,
        "merge_or_imputation_performed": False,
        "primary": {"provider": primary_provider, "snapshot_id": primary_bundle["snapshot_id"]},
        "secondary": {"provider": secondary_provider, "snapshot_id": secondary_bundle["snapshot_id"]},
        "common_return_days": len(common),
        "first_common_day": common[0],
        "last_common_day": common[-1],
        "pearson_return_correlation": numerator / denominator if denominator else None,
        "direction_agreement": sum((a >= 0) == (b >= 0) for a, b in zip(x, y)) / len(common),
        "median_absolute_log_return_difference": median_difference,
        "primary_return_coverage": len(common) / len(left),
        "secondary_return_coverage": len(common) / len(right),
        "interpretation_limit": "instruments may differ by venue, spot/CFD/futures semantics, roll, and close construction",
    }


def comparison_bundle(report: Mapping[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report_id = hashlib.sha256(canonical).hexdigest()
    key = (
        f"{ROOT_PREFIX}/gold/comparisons/instrument={report['instrument_id'].lower()}/"
        f"report-{report_id}.json"
    )
    payload = json.dumps({**report, "report_id": report_id}, sort_keys=True, indent=2).encode()
    return {"snapshot_id": report_id, "manifest_key": key, "manifest": report, "objects": {key: payload}}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the five-instrument daily research lake pilot")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--bucket")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--dukascopy-dir", type=Path,
        help="Directory containing pinned dukascopy-node d1 JSON exports",
    )
    parser.add_argument("--code-version", default="working-tree")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    s3 = None
    if args.bucket:
        import boto3
        s3 = boto3.client("s3")
    results = []
    yahoo_data: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for instrument in INSTRUMENTS:
        try:
            raw, bars = fetch_yahoo_chart(instrument, start=args.start, end=args.end)
            bundle = build_provider_bundle(
                provider="yahoo_chart", instrument=instrument, raw=raw, bars=bars,
                start=args.start, end=args.end, code_version=args.code_version,
            )
            yahoo_data[instrument.instrument_id] = (bundle, bars)
            if s3 is not None:
                publish_bundle(s3_client=s3, bucket=args.bucket, bundle=bundle)
            if args.output_dir is not None:
                export_bundle(root=args.output_dir, bundle=bundle)
            results.append({
                "instrument_id": instrument.instrument_id,
                "status": (
                    "published" if s3 is not None
                    else "exported" if args.output_dir is not None
                    else "validated"
                ),
                "rows": bundle["manifest"]["quality"]["rows"],
                "snapshot_id": bundle["snapshot_id"],
                "manifest_key": bundle["manifest_key"],
            })
        except (ProviderError, ValueError) as exc:
            results.append({
                "instrument_id": instrument.instrument_id,
                "status": "failed",
                "error_type": type(exc).__name__,
            })
    if args.dukascopy_dir is not None:
        for instrument in DUKASCOPY_INSTRUMENTS:
            matches = sorted(args.dukascopy_dir.glob(f"{instrument.vendor_symbol}-d1-bid-*.json"))
            if len(matches) != 1:
                results.append({
                    "instrument_id": instrument.instrument_id,
                    "provider": "dukascopy",
                    "status": "failed",
                    "error_type": "SourceFileCountError",
                })
                continue
            try:
                raw = matches[0].read_bytes()
                bars = parse_dukascopy_json(raw)
                bundle = build_provider_bundle(
                    provider="dukascopy_bid", instrument=instrument, raw=raw, bars=bars,
                    start=args.start, end=args.end, code_version=args.code_version,
                )
                if s3 is not None:
                    publish_bundle(s3_client=s3, bucket=args.bucket, bundle=bundle)
                if args.output_dir is not None:
                    export_bundle(root=args.output_dir, bundle=bundle)
                results.append({
                    "instrument_id": instrument.instrument_id,
                    "provider": "dukascopy_bid",
                    "status": (
                        "published" if s3 is not None
                        else "exported" if args.output_dir is not None
                        else "validated"
                    ),
                    "rows": bundle["manifest"]["quality"]["rows"],
                    "snapshot_id": bundle["snapshot_id"],
                    "manifest_key": bundle["manifest_key"],
                })
                if instrument.instrument_id in ("GC", "CL") and instrument.instrument_id in yahoo_data:
                    yahoo_bundle, yahoo_bars = yahoo_data[instrument.instrument_id]
                    report = compare_provider_returns(
                        instrument_id=instrument.instrument_id,
                        primary_provider="yahoo_chart",
                        primary_bundle=yahoo_bundle,
                        primary_bars=yahoo_bars,
                        secondary_provider="dukascopy_bid",
                        secondary_bundle=bundle,
                        secondary_bars=bars,
                    )
                    report_bundle = comparison_bundle(report)
                    if s3 is not None:
                        publish_bundle(s3_client=s3, bucket=args.bucket, bundle=report_bundle)
                    if args.output_dir is not None:
                        export_bundle(root=args.output_dir, bundle=report_bundle)
                    results.append({
                        "instrument_id": instrument.instrument_id,
                        "provider": "cross_provider_comparison",
                        "status": "published" if s3 is not None else "exported",
                        "report_id": report_bundle["snapshot_id"],
                        "report_key": report_bundle["manifest_key"],
                        "metrics": {
                            "common_return_days": report["common_return_days"],
                            "pearson_return_correlation": report["pearson_return_correlation"],
                            "direction_agreement": report["direction_agreement"],
                        },
                    })
            except (ProviderError, ValueError) as exc:
                results.append({
                    "instrument_id": instrument.instrument_id,
                    "provider": "dukascopy_bid",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                })
    print(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "provider": "yahoo_chart",
        "research_only": True,
        "published": s3 is not None,
        "results": results,
    }, indent=2))
    return 0 if all(item["status"] != "failed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
