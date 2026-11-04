"""Run one resumable G1 research inference session, then exit."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from .database import Database
from .eami_inference_ledger import (
    TIMEFRAME_SECONDS,
    get_inference_status,
    record_observation,
    record_outcome,
)


class EAMIInferenceCLIError(ValueError):
    pass


def _file_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise EAMIInferenceCLIError(
            "G1 CLI currently supports only file:// feature assets"
        )
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_rows(path: Path) -> dict[str, dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["timestamp", "close", "return_1"])
    rows = table.to_pylist()
    result = {}
    for row in rows:
        timestamp = row["timestamp"]
        if timestamp.tzinfo is None:
            raise EAMIInferenceCLIError("feature timestamps must include UTC timezone")
        result[timestamp.isoformat()] = row
    return result


def run_session(
    *, db: Database, session_id: str, through_at: str, observed_at: str
) -> dict:
    with db.connection() as connection:
        session = connection.execute(
            """SELECT s.*, m.model_family, m.feature_version,
               a.storage_uri, a.content_sha256
               FROM eami_inference_sessions s
               JOIN eami_model_versions m ON m.id = s.model_id
               JOIN eami_data_assets a ON a.id = s.input_asset_id
               WHERE s.id = ?""",
            (session_id,),
        ).fetchone()
    if session is None:
        raise EAMIInferenceCLIError("inference session is not registered")
    if session["model_family"] != "baseline_return":
        raise EAMIInferenceCLIError("G1 CLI only supports baseline_return models")
    if session["feature_version"] != "g1-return-v1":
        raise EAMIInferenceCLIError("G1 CLI requires feature version g1-return-v1")
    path = _file_path(session["storage_uri"])
    if not path.is_file() or _sha256(path) != session["content_sha256"]:
        raise EAMIInferenceCLIError("feature asset file failed integrity verification")
    rows = _load_rows(path)
    status = get_inference_status(db, session_id, through_at=through_at)
    created = {"predictions": 0, "failures": 0, "outcomes": 0}
    for completed_at in status["missing_expected_at"]:
        row = rows.get(completed_at)
        try:
            if row is None or row["return_1"] is None:
                raise EAMIInferenceCLIError("feature row is unavailable")
            value = float(row["return_1"])
            direction = "UP" if value > 0 else "DOWN" if value < 0 else "NEUTRAL"
            score = min(0.99, 0.5 + abs(value) * 10.0)
            input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "timestamp": completed_at,
                        "close": row["close"],
                        "return_1": value,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            record_observation(
                db,
                {
                    "session_id": session_id,
                    "completed_at": completed_at,
                    "status": "PREDICTION",
                    "input_sha256": input_hash,
                    "direction": direction,
                    "score": score,
                    "prediction": {
                        "feature": "return_1",
                        "feature_value": value,
                        "calibrated_probability": False,
                    },
                    "error_code": None,
                },
            )
            created["predictions"] += 1
        except (EAMIInferenceCLIError, TypeError, ValueError):
            record_observation(
                db,
                {
                    "session_id": session_id,
                    "completed_at": completed_at,
                    "status": "FAILURE",
                    "input_sha256": None,
                    "direction": None,
                    "score": None,
                    "prediction": {},
                    "error_code": "FEATURE_DATA_UNAVAILABLE",
                },
            )
            created["failures"] += 1

    seconds = TIMEFRAME_SECONDS[session["timeframe"]]
    with db.connection() as connection:
        pending = connection.execute(
            """SELECT o.* FROM eami_inference_observations o
               LEFT JOIN eami_inference_outcomes x ON x.observation_id = o.id
               WHERE o.session_id = ? AND o.status = 'PREDICTION'
                 AND x.id IS NULL AND o.target_at <= ? ORDER BY o.completed_at""",
            (session_id, observed_at),
        ).fetchall()
    for prediction in pending:
        opened = datetime.fromisoformat(prediction["completed_at"])
        target_row_at = (opened + timedelta(seconds=seconds)).isoformat()
        start_row, target_row = (
            rows.get(prediction["completed_at"]),
            rows.get(target_row_at),
        )
        if start_row is None or target_row is None:
            continue
        actual_return = float(target_row["close"]) / float(start_row["close"]) - 1.0
        label = (
            "UP" if actual_return > 0 else "DOWN" if actual_return < 0 else "NEUTRAL"
        )
        record_outcome(
            db,
            prediction["id"],
            label=label,
            actual_return=actual_return,
            outcome={
                "start_close": start_row["close"],
                "target_close": target_row["close"],
            },
            observed_at=observed_at,
        )
        created["outcomes"] += 1
    final = get_inference_status(db, session_id, through_at=through_at)
    return {
        "session_id": session_id,
        **created,
        "remaining_gaps": len(final["missing_expected_at"]),
        "order_permission": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--through-at", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args()
    result = run_session(
        db=Database(str(args.db)),
        session_id=args.session_id,
        through_at=args.through_at,
        observed_at=args.observed_at,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
