"""Build fail-closed CPU/GPU artifacts for the six-hour strategy briefing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .schemas import Bar


NODE_SCHEMA = "emergenzix-analysis-node-result/v1"
EVIDENCE_SCHEMA = "emergenzix-strategy-evidence/v1"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("record_sha256", None)
    return canonical_sha256(payload)


def build_strategy_analysis(
    market_payload: Mapping[str, Any], *, generated_at: datetime,
    source_revision: str, independent_decisions: int = 0,
    minimum_decisions_before_change: int = 180,
) -> dict[str, Any]:
    """Calculate the frozen Turtle 20/10 candidate from completed bars."""
    bars = _validated_bars(market_payload)
    if len(bars) < 56:
        raise ValueError("strategy analysis requires at least 56 completed bars")
    current = bars[-1]
    entry = max(bar.high for bar in bars[-21:-1])
    exit_level = min(bar.low for bar in bars[-11:-1])
    decision = "BUY" if current.close > entry else "HOLD"
    reason = (
        f"Completed close {current.close:.8f} exceeded prior 20-bar high {entry:.8f}."
        if decision == "BUY"
        else f"Completed close {current.close:.8f} did not exceed prior 20-bar high {entry:.8f}."
    )
    result: dict[str, Any] = {
        "schema_version": NODE_SCHEMA,
        "role": "strategy_analysis",
        "generated_at": _utc(generated_at).isoformat(),
        "source_revision": source_revision,
        "market": _market_identity(market_payload, bars),
        "strategy": {
            "name": "turtle_breakout_20_10",
            "version": "v1",
            "decision": decision,
            "reason": reason,
            "entry_level": float(entry),
            "exit_level": float(exit_level),
            "position_assumption": "FLAT",
        },
        "portfolio": {
            "position": "UNAVAILABLE_ON_ANALYSIS_NODE",
            "exposure_pct": None,
            "authoritative_runtime": "hong_kong_paper_node",
        },
        "risk": {
            "status": "REVIEW_REQUIRED" if decision == "BUY" else "PASS",
            "stale_data": False,
            "execution_permission": False,
        },
        "evaluation": {
            "paper_vs_backtest_deviation": "awaiting_authoritative_paper_reconciliation",
            "independent_decisions": independent_decisions,
            "minimum_decisions_before_change": minimum_decisions_before_change,
        },
        "order_permission": False,
    }
    result["record_sha256"] = record_sha256(result)
    return result


def build_model_prediction(
    signal_manifest: Mapping[str, Any], *, expected_market: Mapping[str, Any],
    generated_at: datetime, source_revision: str,
) -> dict[str, Any]:
    """Normalize a real Chronos signal without granting it trading authority."""
    signals = signal_manifest.get("signals")
    if not isinstance(signals, list) or len(signals) != 1 or not isinstance(signals[0], Mapping):
        raise ValueError("exactly one model signal is required")
    signal = signals[0]
    model, input_data, prediction = signal.get("model"), signal.get("input"), signal.get("prediction")
    if not all(isinstance(item, Mapping) for item in (model, input_data, prediction)):
        raise ValueError("model signal objects are incomplete")
    if signal.get("order_permission") is not False:
        raise ValueError("model signal must deny order permission")
    if signal.get("record_sha256") != _external_signal_sha256(signal):
        raise ValueError("model signal hash mismatch")
    for field in ("symbol", "timeframe", "bars_sha256", "end_at"):
        expected = expected_market.get(field)
        actual = input_data.get(field)
        if actual != expected:
            raise ValueError(f"model signal {field} mismatch")
    direction = prediction.get("direction")
    if direction not in {"UP", "DOWN", "NEUTRAL"}:
        raise ValueError("model prediction direction is invalid")
    result: dict[str, Any] = {
        "schema_version": NODE_SCHEMA,
        "role": "model_prediction",
        "generated_at": _utc(generated_at).isoformat(),
        "source_revision": source_revision,
        "market": dict(expected_market),
        "prediction": {
            "direction": direction,
            "horizon": str(expected_market["timeframe"]),
            "target_at": prediction.get("target_at"),
            "forecast_close": prediction.get("forecast_close"),
            "predicted_return": prediction.get("predicted_return"),
            "confidence": "unscaled",
            "model": model.get("name"),
            "model_version": model.get("version"),
            "model_record_sha256": signal["record_sha256"],
        },
        "order_permission": False,
    }
    result["record_sha256"] = record_sha256(result)
    return result


def merge_node_results(
    strategy: Mapping[str, Any], prediction: Mapping[str, Any], *, now: datetime,
) -> dict[str, Any]:
    """Merge only fresh, untampered results built from the exact same bars."""
    checked_strategy = _verify_node(strategy, role="strategy_analysis", now=now)
    checked_prediction = _verify_node(prediction, role="model_prediction", now=now)
    if checked_strategy["market"] != checked_prediction["market"]:
        raise ValueError("CPU and GPU market identities do not match")
    generated_at = max(
        _timestamp(checked_strategy["generated_at"]),
        _timestamp(checked_prediction["generated_at"]),
    )
    evidence: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "market": checked_strategy["market"],
        "strategy": checked_strategy["strategy"],
        "prediction": checked_prediction["prediction"],
        "portfolio": checked_strategy["portfolio"],
        "risk": checked_strategy["risk"],
        "evaluation": checked_strategy["evaluation"],
        "provenance": [
            {"node": "aws-cpu", "role": "strategy_analysis",
             "revision": checked_strategy["source_revision"],
             "record_sha256": checked_strategy["record_sha256"]},
            {"node": "aws-gpu", "role": "model_prediction",
             "revision": checked_prediction["source_revision"],
             "record_sha256": checked_prediction["record_sha256"]},
        ],
        "order_permission": False,
    }
    evidence["record_sha256"] = record_sha256(evidence)
    return evidence


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _validated_bars(payload: Mapping[str, Any]) -> list[Bar]:
    raw = payload.get("bars")
    if not isinstance(raw, list):
        raise ValueError("market payload must contain bars")
    bars = [Bar.model_validate(item) for item in raw]
    if any(current.timestamp <= previous.timestamp for previous, current in zip(bars, bars[1:])):
        raise ValueError("market bars must be strictly ordered")
    return bars


def _market_identity(payload: Mapping[str, Any], bars: list[Bar]) -> dict[str, Any]:
    normalized = [{"timestamp": bar.timestamp.isoformat(), "open": float(bar.open),
                   "high": float(bar.high), "low": float(bar.low), "close": float(bar.close),
                   "volume": float(bar.volume)} for bar in bars]
    return {
        "exchange": str(payload.get("exchange", "binance")),
        "symbol": str(payload.get("symbol", "")),
        "timeframe": str(payload.get("timeframe", "")),
        "completed_at": bars[-1].timestamp.isoformat(),
        "end_at": bars[-1].timestamp.isoformat(),
        "reference_close": float(bars[-1].close),
        "bars_count": len(bars),
        "bars_sha256": canonical_sha256(normalized),
    }


def _verify_node(value: Mapping[str, Any], *, role: str, now: datetime) -> dict[str, Any]:
    result = dict(value)
    if result.get("schema_version") != NODE_SCHEMA or result.get("role") != role:
        raise ValueError(f"invalid {role} schema or role")
    if result.get("record_sha256") != record_sha256(result):
        raise ValueError(f"{role} hash mismatch")
    generated_at = _timestamp(result.get("generated_at"))
    active_now = _utc(now)
    if generated_at > active_now + timedelta(minutes=5) or active_now - generated_at > timedelta(hours=8):
        raise ValueError(f"{role} is stale")
    if result.get("order_permission") is not False:
        raise ValueError(f"{role} must deny order permission")
    return result


def _external_signal_sha256(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("record_sha256", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
