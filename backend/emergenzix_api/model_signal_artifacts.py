"""Fail-closed verification for offline model inference signal artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .schemas import Bar


SCHEMA_VERSION = "emergenzix-model-signal/v1"
EXPECTED_FAMILIES = ("Chronos", "Qlib")
TIMEFRAME_SECONDS = {"15m": 900, "1h": 3_600, "4h": 14_400}


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bars_sha256(bars: Sequence[Bar]) -> str:
    return _canonical_sha256(
        [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
            for bar in bars
        ]
    )


def signal_record_sha256(record: Mapping[str, Any]) -> str:
    payload = dict(record)
    payload.pop("record_sha256", None)
    return _canonical_sha256(payload)


def build_signal_record(
    *, family: str, name: str, version: str, source_revision: str,
    artifact_sha256: str, bars: Sequence[Bar], symbol: str, timeframe: str,
    generated_at: datetime, direction: str, score: float, target_at: datetime,
    prediction_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a self-hashed advisory record for an offline model runner."""
    prediction = {
        "target_at": target_at.isoformat(),
        "direction": direction,
        "score": score,
        "calibrated_probability": False,
        **dict(prediction_details or {}),
    }
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "model": {
            "family": family,
            "name": name,
            "version": version,
            "source_revision": source_revision,
            "artifact_sha256": artifact_sha256,
        },
        "input": {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars_count": len(bars),
            "bars_sha256": bars_sha256(bars),
            "start_at": bars[0].timestamp.isoformat(),
            "end_at": bars[-1].timestamp.isoformat(),
        },
        "prediction": prediction,
        "order_permission": False,
    }
    record["record_sha256"] = signal_record_sha256(record)
    return record


def _timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def verify_signal_record(
    record: Mapping[str, Any], *, bars: Sequence[Bar], symbol: str,
    timeframe: str, now: datetime,
) -> dict[str, Any]:
    """Verify one inference output against the exact completed input window."""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported model signal schema")
    if record.get("record_sha256") != signal_record_sha256(record):
        raise ValueError("model signal record hash mismatch")
    model = record.get("model")
    input_data = record.get("input")
    prediction = record.get("prediction")
    if not all(isinstance(item, Mapping) for item in (model, input_data, prediction)):
        raise ValueError("model, input, and prediction objects are required")
    family = str(model.get("family", ""))
    if family not in EXPECTED_FAMILIES:
        raise ValueError("model family is not allowlisted")
    for field in ("name", "version", "source_revision", "artifact_sha256"):
        if not str(model.get(field, "")).strip():
            raise ValueError(f"model.{field} is required")
    artifact_sha256 = str(model["artifact_sha256"])
    if len(artifact_sha256) != 64 or any(char not in "0123456789abcdef" for char in artifact_sha256):
        raise ValueError("model.artifact_sha256 must be lowercase SHA-256")

    if input_data.get("symbol") != symbol or input_data.get("timeframe") != timeframe:
        raise ValueError("model signal market does not match request")
    if input_data.get("bars_count") != len(bars):
        raise ValueError("model signal input bar count mismatch")
    if input_data.get("bars_sha256") != bars_sha256(bars):
        raise ValueError("model signal input snapshot hash mismatch")
    input_start = _timestamp(input_data.get("start_at"), "input.start_at")
    if input_start != bars[0].timestamp.astimezone(timezone.utc):
        raise ValueError("model signal input start timestamp mismatch")
    input_end = _timestamp(input_data.get("end_at"), "input.end_at")
    if input_end != bars[-1].timestamp.astimezone(timezone.utc):
        raise ValueError("model signal input end timestamp mismatch")

    generated_at = _timestamp(record.get("generated_at"), "generated_at")
    target_at = _timestamp(prediction.get("target_at"), "prediction.target_at")
    interval = TIMEFRAME_SECONDS.get(timeframe)
    if interval is None:
        raise ValueError("unsupported model signal timeframe")
    if generated_at < input_end or generated_at > now + timedelta(minutes=5):
        raise ValueError("model signal generation timestamp is invalid")
    if now - generated_at > timedelta(seconds=interval * 2):
        raise ValueError("model signal artifact is stale")
    if target_at != input_end + timedelta(seconds=interval):
        raise ValueError("model signal target timestamp is not the next bar")
    if prediction.get("direction") not in {"UP", "DOWN", "NEUTRAL"}:
        raise ValueError("model signal direction is invalid")
    if prediction.get("calibrated_probability") is not False:
        raise ValueError("model signal must not claim calibrated probability")
    score = float(prediction.get("score"))
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("model signal score must be finite and between zero and one")
    if record.get("order_permission") is not False:
        raise ValueError("model signal must deny order permission")
    return {
        "name": str(model["name"]),
        "family": family,
        "version": str(model["version"]),
        "source_revision": str(model["source_revision"]),
        "artifact_sha256": artifact_sha256,
        "record_sha256": str(record["record_sha256"]),
        "status": "verified",
        "direction": prediction["direction"],
        "score": score,
        "generated_at": generated_at.isoformat(),
        "target_at": target_at.isoformat(),
        "order_permission": False,
    }


def load_verified_model_signals(
    path: str | Path | None, *, bars: Sequence[Bar], symbol: str,
    timeframe: str, now: datetime,
) -> list[dict[str, Any]]:
    """Load a bounded local manifest and report per-family verification status."""
    records: list[Mapping[str, Any]] = []
    load_error: str | None = None
    if path:
        try:
            artifact_path = Path(path)
            if artifact_path.stat().st_size > 1_000_000:
                raise ValueError("model signal artifact exceeds one megabyte")
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping) or not isinstance(payload.get("signals"), list):
                raise ValueError("model signal artifact must contain a signals list")
            records = [item for item in payload["signals"] if isinstance(item, Mapping)]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            load_error = str(exc)

    statuses: list[dict[str, Any]] = []
    for family in EXPECTED_FAMILIES:
        candidates = []
        for item in records:
            model = item.get("model")
            if isinstance(model, Mapping) and model.get("family") == family:
                candidates.append(item)
        if not candidates:
            statuses.append({
                "name": family,
                "family": family,
                "status": "artifact_unavailable" if load_error is None else "artifact_rejected",
                "reason": load_error or "no matching inference record",
                "order_permission": False,
            })
            continue
        try:
            statuses.append(verify_signal_record(
                candidates[-1], bars=bars, symbol=symbol, timeframe=timeframe, now=now
            ))
        except (TypeError, ValueError) as exc:
            statuses.append({
                "name": family,
                "family": family,
                "status": "artifact_rejected",
                "reason": str(exc),
                "order_permission": False,
            })
    return statuses
