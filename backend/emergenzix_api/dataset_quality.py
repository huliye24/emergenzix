"""Read-only quality diagnostics for registered OHLCV datasets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored dataset timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def build_dataset_quality_report(
    *,
    dataset_id: str,
    sha256: str,
    symbol: str,
    timeframe: str | None,
    bars_json: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize candle spacing and recency without mutating or rejecting data."""
    bars = json.loads(bars_json)
    timestamps = [_parse_utc_timestamp(bar["timestamp"]) for bar in bars]
    interval_seconds = TIMEFRAME_SECONDS.get(timeframe or "")
    gaps: list[dict[str, Any]] = []
    irregular_intervals: list[dict[str, Any]] = []
    missing_candles = 0

    if interval_seconds is not None:
        # Small timestamp tolerance handles serialization precision without hiding
        # a meaningful fraction of a candle interval.
        tolerance_seconds = max(1.0, interval_seconds * 0.001)
        for previous, current in zip(timestamps, timestamps[1:]):
            observed_seconds = (current - previous).total_seconds()
            interval_multiple = round(observed_seconds / interval_seconds)
            is_aligned = (
                interval_multiple >= 1
                and abs(observed_seconds - interval_multiple * interval_seconds)
                <= tolerance_seconds
            )
            record = {
                "after": previous.isoformat().replace("+00:00", "Z"),
                "before": current.isoformat().replace("+00:00", "Z"),
                "observed_interval_seconds": round(observed_seconds, 3),
                "expected_interval_seconds": interval_seconds,
            }
            if not is_aligned:
                irregular_intervals.append(record)
            elif interval_multiple > 1:
                missing = interval_multiple - 1
                gaps.append({**record, "missing_candles_estimate": missing})
                missing_candles += missing

    expected_bars_count: int | None = None
    coverage_pct: float | None = None
    if interval_seconds is not None and not irregular_intervals:
        expected_bars_count = (
            round((timestamps[-1] - timestamps[0]).total_seconds() / interval_seconds)
            + 1
        )
        if expected_bars_count > 0:
            coverage_pct = round(len(timestamps) / expected_bars_count * 100.0, 6)

    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    latest_age_seconds = (checked_at - timestamps[-1]).total_seconds()
    if interval_seconds is None:
        freshness_status = "not_assessable"
        freshness_threshold_seconds = None
    elif latest_age_seconds < 0:
        freshness_status = "future_timestamp"
        freshness_threshold_seconds = interval_seconds * 2
    elif latest_age_seconds <= interval_seconds * 2:
        freshness_status = "within_threshold"
        freshness_threshold_seconds = interval_seconds * 2
    else:
        freshness_status = "stale"
        freshness_threshold_seconds = interval_seconds * 2

    return {
        "dataset_id": dataset_id,
        "sha256": sha256,
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_count": len(bars),
        "start_at": timestamps[0].isoformat().replace("+00:00", "Z"),
        "end_at": timestamps[-1].isoformat().replace("+00:00", "Z"),
        "interval_seconds": interval_seconds,
        "expected_bars_count": expected_bars_count,
        "missing_candles_estimate": (
            missing_candles if interval_seconds is not None else None
        ),
        "coverage_pct": coverage_pct,
        "gaps": gaps,
        "irregular_intervals": irregular_intervals,
        "freshness": {
            "status": freshness_status,
            "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
            "latest_bar_age_seconds": round(latest_age_seconds, 3),
            "threshold_seconds": freshness_threshold_seconds,
        },
        "diagnostic_only": True,
        "notes": [
            "Quality checks are diagnostic and do not modify or reject the dataset.",
            "Freshness compares the latest candle timestamp with the current UTC time; it is a heuristic, not a trading gate.",
            "Coverage is omitted when candle spacing is irregular or the dataset has no declared timeframe.",
        ],
    }
