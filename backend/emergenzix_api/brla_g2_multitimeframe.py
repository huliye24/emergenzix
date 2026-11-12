"""Frozen causal 15m/1h/4h/1d observation alignment for BRLA-G2."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

PROTOCOL = "emergenzix-brla-g2-multitimeframe-observation-v1"
BASE_INTERVAL = timedelta(minutes=15)
TIMEFRAMES = {"15m": 1, "1h": 4, "4h": 16, "1d": 96}
WINDOW_BARS = {"15m": 192, "1h": 168, "4h": 180, "1d": 180}


@dataclass(frozen=True, slots=True)
class AlignedBar:
    open_time: str
    close_time: str
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    source_bar_count: int


@dataclass(frozen=True, slots=True)
class TimeframeWindow:
    timeframe: str
    expected_bars: int
    bars: tuple[AlignedBar, ...]
    window_sha256: str


@dataclass(frozen=True, slots=True)
class MultiTimeframeObservation:
    protocol: str
    as_of: str
    source_interval: str
    windows: Mapping[str, TimeframeWindow]
    observation_sha256: str
    causal: bool = True
    future_fields_present: bool = False
    mode: str = "RESEARCH"
    position_permission: bool = False
    order_permission: bool = False


def _parse_time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _floor(open_time: datetime, source_count: int) -> datetime:
    seconds = source_count * int(BASE_INTERVAL.total_seconds())
    epoch_seconds = int(open_time.timestamp())
    return datetime.fromtimestamp(epoch_seconds - epoch_seconds % seconds, tz=timezone.utc)


def _canonical_source(source: Mapping[str, Any]) -> tuple[datetime, dict[str, float]]:
    timestamp = _parse_time(source["timestamp"])
    if timestamp.second or timestamp.microsecond or timestamp.minute % 15:
        raise ValueError("source timestamps must align to 15-minute UTC boundaries")
    if source.get("is_closed") is not True or source.get("is_imputed") is True:
        raise ValueError("only completed, non-imputed source bars are accepted")
    row = {name: float(source[name]) for name in
           ("open", "high", "low", "close", "base_volume")}
    if (any(not math.isfinite(value) or value <= 0 for value in row.values())
            or row["low"] > min(row["open"], row["close"])
            or row["high"] < max(row["open"], row["close"])):
        raise ValueError("source OHLCV must be finite, positive and internally valid")
    return timestamp, row


def _aggregate(source: Sequence[tuple[datetime, dict[str, float]]],
               *, source_count: int, as_of: datetime) -> tuple[AlignedBar, ...]:
    groups: dict[datetime, list[tuple[datetime, dict[str, float]]]] = {}
    for timestamp, row in source:
        group = _floor(timestamp, source_count)
        groups.setdefault(group, []).append((timestamp, row))
    result = []
    duration = BASE_INTERVAL * source_count
    for group_open in sorted(groups):
        group_close = group_open + duration
        rows = groups[group_open]
        expected_times = [group_open + BASE_INTERVAL * index for index in range(source_count)]
        if group_close > as_of:
            continue
        if len(rows) != source_count or [timestamp for timestamp, _ in rows] != expected_times:
            raise ValueError("an aggregate interval is incomplete or misaligned")
        values = [row for _, row in rows]
        result.append(AlignedBar(group_open.isoformat(), group_close.isoformat(),
            values[0]["open"], max(row["high"] for row in values),
            min(row["low"] for row in values), values[-1]["close"],
            sum(row["base_volume"] for row in values), source_count))
    return tuple(result)


def build_multitimeframe_observation(source_bars: Sequence[Mapping[str, Any]], *,
                                     as_of: datetime) -> MultiTimeframeObservation:
    decision_time = _parse_time(as_of)
    if (decision_time.second or decision_time.microsecond
            or decision_time.minute % 15):
        raise ValueError("decision time must align to a completed 15-minute boundary")
    causal_sources = [row for row in source_bars
                      if _parse_time(row["timestamp"]) + BASE_INTERVAL <= decision_time]
    parsed = sorted((_canonical_source(row) for row in causal_sources), key=lambda row: row[0])
    timestamps = [timestamp for timestamp, _ in parsed]
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("duplicate source bars are forbidden")
    required_start = _floor(decision_time, TIMEFRAMES["1d"]) - timedelta(
        days=WINDOW_BARS["1d"])
    required = [row for row in parsed if row[0] >= required_start]
    expected_count = int((decision_time - required_start) / BASE_INTERVAL)
    expected_times = [required_start + BASE_INTERVAL * index for index in range(expected_count)]
    if [timestamp for timestamp, _ in required] != expected_times:
        raise ValueError("the causal source window has gaps, duplicates or insufficient history")

    windows: dict[str, TimeframeWindow] = {}
    for timeframe, source_count in TIMEFRAMES.items():
        aggregated = _aggregate(required, source_count=source_count, as_of=decision_time)
        expected = WINDOW_BARS[timeframe]
        selected = aggregated[-expected:]
        if len(selected) != expected:
            raise ValueError(f"{timeframe} has insufficient complete bars")
        payload = [asdict(row) for row in selected]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        windows[timeframe] = TimeframeWindow(timeframe, expected, selected, digest)
    identity = {"protocol": PROTOCOL, "as_of": decision_time.isoformat(),
                "source_interval": "15m",
                "windows": {name: window.window_sha256 for name, window in windows.items()},
                "causal": True, "future_fields_present": False,
                "position_permission": False, "order_permission": False}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return MultiTimeframeObservation(PROTOCOL, decision_time.isoformat(), "15m",
                                     windows, digest)


def stage1_feature_fingerprints(observation: MultiTimeframeObservation) -> dict[str, str]:
    if observation.protocol != PROTOCOL or not observation.causal or observation.future_fields_present:
        raise ValueError("only verified causal BRLA-G2 observations are accepted")
    return {timeframe: observation.windows[timeframe].window_sha256
            for timeframe in TIMEFRAMES}


def observation_protocol_manifest() -> dict[str, Any]:
    payload = {"protocol": PROTOCOL, "source_interval": "15m",
        "timeframe_source_counts": TIMEFRAMES, "window_bars": WINDOW_BARS,
        "utc_boundaries": True, "completed_only": True, "imputed_allowed": False,
        "gap_policy": "FAIL_CLOSED", "partial_aggregate_policy": "EXCLUDE",
        "future_fields_present": False, "position_permission": False,
        "order_permission": False}
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
