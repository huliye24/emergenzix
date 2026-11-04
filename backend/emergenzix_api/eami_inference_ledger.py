"""Resumable, append-only research inference ledger."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database


class EAMIInferenceError(ValueError):
    pass


TIMEFRAME_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
FAILURE_CODES = {
    "MARKET_DATA_UNAVAILABLE",
    "FEATURE_DATA_UNAVAILABLE",
    "MODEL_LOAD_FAILED",
    "INFERENCE_FAILED",
    "INVALID_OUTPUT",
}


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EAMIInferenceError("ledger values must be finite JSON") from exc


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise EAMIInferenceError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _aligned(value: datetime, seconds: int) -> bool:
    return int(value.timestamp()) % seconds == 0 and value.microsecond == 0


def create_inference_session(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    seconds = TIMEFRAME_SECONDS.get(payload["timeframe"])
    if seconds is None:
        raise EAMIInferenceError("unsupported inference timeframe")
    first = _parse(payload["first_expected_at"])
    if not _aligned(first, seconds):
        raise EAMIInferenceError("first_expected_at must align to the timeframe")
    created_at = _now()
    record = {
        **payload,
        "first_expected_at": first.isoformat(),
        "created_at": created_at,
    }
    session_id = _hash(record)
    with db.connection() as connection:
        model = connection.execute(
            "SELECT feature_version FROM eami_model_versions WHERE id = ?",
            (payload["model_id"],),
        ).fetchone()
        asset = connection.execute(
            "SELECT layer, feature_version, symbol, timeframe FROM eami_data_assets WHERE id = ?",
            (payload["input_asset_id"],),
        ).fetchone()
        if model is None or asset is None:
            raise EAMIInferenceError("model and input asset must already be registered")
        if asset["layer"] != "FEATURE":
            raise EAMIInferenceError("inference sessions require a FEATURE input asset")
        if asset["feature_version"] != model["feature_version"]:
            raise EAMIInferenceError("model and input asset feature versions differ")
        if asset["symbol"] and asset["symbol"] != payload["symbol"]:
            raise EAMIInferenceError("input asset symbol differs from the session")
        if asset["timeframe"] and asset["timeframe"] != payload["timeframe"]:
            raise EAMIInferenceError("input asset timeframe differs from the session")
        connection.execute(
            """INSERT INTO eami_inference_sessions
               (id, model_id, input_asset_id, symbol, timeframe, horizon_bars,
                first_expected_at, record_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                payload["model_id"],
                payload["input_asset_id"],
                payload["symbol"],
                payload["timeframe"],
                payload["horizon_bars"],
                first.isoformat(),
                session_id,
                created_at,
            ),
        )
    return get_inference_status(db, session_id, through_at=first.isoformat())


def missing_expected_times(
    db: Database, session_id: str, *, through_at: str, limit: int = 1000
) -> list[str]:
    through = _parse(through_at)
    with db.connection() as connection:
        session = connection.execute(
            "SELECT * FROM eami_inference_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise EAMIInferenceError("inference session is not registered")
        recorded = {
            row["completed_at"]
            for row in connection.execute(
                "SELECT completed_at FROM eami_inference_observations WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        }
    seconds = TIMEFRAME_SECONDS[session["timeframe"]]
    first = _parse(session["first_expected_at"])
    if not _aligned(through, seconds) or through < first:
        raise EAMIInferenceError(
            "through_at must be aligned and not precede the session"
        )
    missing = []
    current = first
    while current <= through:
        stamp = current.isoformat()
        if stamp not in recorded:
            missing.append(stamp)
            if len(missing) >= limit:
                break
        current += timedelta(seconds=seconds)
    return missing


def record_observation(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    completed = _parse(payload["completed_at"])
    created_at = _now()
    with db.connection() as connection:
        session = connection.execute(
            "SELECT * FROM eami_inference_sessions WHERE id = ?",
            (payload["session_id"],),
        ).fetchone()
        if session is None:
            raise EAMIInferenceError("inference session is not registered")
        seconds = TIMEFRAME_SECONDS[session["timeframe"]]
        if not _aligned(completed, seconds) or completed < _parse(
            session["first_expected_at"]
        ):
            raise EAMIInferenceError("completed_at is outside the session grid")
        # completed_at follows the repository convention of storing the open
        # timestamp of a candle that has already closed.  A one-bar-ahead label
        # is therefore available only after the next candle also closes.
        target = completed + timedelta(
            seconds=seconds * (int(session["horizon_bars"]) + 1)
        )
        status = payload["status"]
        if status not in {"PREDICTION", "FAILURE"}:
            raise EAMIInferenceError("observation status is invalid")
        if status == "PREDICTION":
            if payload.get("direction") not in {"UP", "DOWN", "NEUTRAL"}:
                raise EAMIInferenceError("prediction direction is invalid")
            if payload.get("score") is None or payload.get("input_sha256") is None:
                raise EAMIInferenceError("predictions require score and input_sha256")
            score = float(payload["score"])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise EAMIInferenceError(
                    "prediction score must be between zero and one"
                )
            if len(payload["input_sha256"]) != 64:
                raise EAMIInferenceError("input_sha256 must be a SHA-256")
            prediction = payload.get("prediction", {})
            error_code = None
        else:
            if payload.get("error_code") not in FAILURE_CODES:
                raise EAMIInferenceError("unsupported inference failure code")
            score, prediction, error_code = None, None, payload["error_code"]
        record = {
            "session_id": payload["session_id"],
            "completed_at": completed.isoformat(),
            "target_at": target.isoformat(),
            "status": status,
            "input_sha256": payload.get("input_sha256")
            if status == "PREDICTION"
            else None,
            "direction": payload.get("direction") if status == "PREDICTION" else None,
            "score": score,
            "prediction": prediction,
            "error_code": error_code,
            "created_at": created_at,
        }
        observation_id = _hash(record)
        try:
            connection.execute(
                """INSERT INTO eami_inference_observations
                   (id, session_id, completed_at, target_at, status, input_sha256,
                    direction, score, prediction_json, error_code, record_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation_id,
                    record["session_id"],
                    record["completed_at"],
                    record["target_at"],
                    status,
                    record["input_sha256"],
                    record["direction"],
                    score,
                    None if prediction is None else _canonical(prediction),
                    error_code,
                    observation_id,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise EAMIInferenceError(
                "this session already recorded the completed candle"
            ) from exc
    return {
        "id": observation_id,
        **record,
        "record_sha256": observation_id,
        "order_permission": False,
    }


def record_outcome(
    db: Database,
    observation_id: str,
    *,
    label: str,
    actual_return: float,
    outcome: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    observed = _parse(observed_at)
    if label not in {"UP", "DOWN", "NEUTRAL"} or not math.isfinite(actual_return):
        raise EAMIInferenceError("outcome label or return is invalid")
    with db.connection() as connection:
        prediction = connection.execute(
            "SELECT * FROM eami_inference_observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if prediction is None or prediction["status"] != "PREDICTION":
            raise EAMIInferenceError("outcomes require an existing prediction")
        if observed < _parse(prediction["target_at"]):
            raise EAMIInferenceError(
                "outcome cannot be recorded before the prediction horizon"
            )
        created_at = _now()
        record = {
            "observation_id": observation_id,
            "label": label,
            "actual_return": actual_return,
            "outcome": outcome,
            "created_at": created_at,
        }
        outcome_id = _hash(record)
        try:
            connection.execute(
                """INSERT INTO eami_inference_outcomes
                   (id, observation_id, label, actual_return, outcome_json,
                    record_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    outcome_id,
                    observation_id,
                    label,
                    actual_return,
                    _canonical(outcome),
                    outcome_id,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise EAMIInferenceError("prediction outcome is already recorded") from exc
    return {"id": outcome_id, **record, "record_sha256": outcome_id}


def get_inference_status(
    db: Database, session_id: str, *, through_at: str
) -> dict[str, Any]:
    with db.connection() as connection:
        session = connection.execute(
            "SELECT * FROM eami_inference_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise EAMIInferenceError("inference session is not registered")
        counts = connection.execute(
            """SELECT COUNT(*) total,
               SUM(status = 'PREDICTION') predictions, SUM(status = 'FAILURE') failures,
               MAX(completed_at) checkpoint FROM eami_inference_observations
               WHERE session_id = ?""",
            (session_id,),
        ).fetchone()
    missing = missing_expected_times(db, session_id, through_at=through_at)
    return {
        "session": dict(session),
        "checkpoint_completed_at": counts["checkpoint"],
        "observation_count": int(counts["total"] or 0),
        "prediction_count": int(counts["predictions"] or 0),
        "failure_count": int(counts["failures"] or 0),
        "missing_expected_at": missing,
        "order_permission": False,
    }
