"""Read-only verifier for GPU Alpha Lab E001 output artifacts.

Checks artifact digests, split metadata, prediction chronology and reported
holdout metrics. Does not load model weights or access the dataset or GPU.
Self-consistency is not proof of source authenticity or future profitability.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import EXPERIMENT_PROTOCOL


ARTIFACT_FILES = (
    "manifest.json",
    "metrics.json",
    "test_predictions.json",
    "model_weights.pt",
)
_MAX_JSON_BYTES = 128 * 1024 * 1024
_TIMEFRAME_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400}
_FEATURE_NAMES = [
    "log_return",
    "log_high_low",
    "log_close_open",
    "log1p_volume_change",
]


class ArtifactVerificationError(ValueError):
    """GPU Alpha Lab evidence is incomplete or inconsistent."""


def _require(condition: bool, description: str) -> None:
    if not condition:
        raise ArtifactVerificationError(description)


def _object(value: Any, description: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{description} must be an object")
    return value


def _integer(value: Any, description: str, *, minimum: int = 1) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{description} must be an integer >= {minimum}",
    )
    return value


def _number(value: Any, description: str) -> float:
    _require(
        isinstance(value, (float, int)) and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{description} must be finite",
    )
    return float(value)


def _digest(value: Any, description: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64
        and all(char in "0123456789abcdef" for char in value),
        f"{description} must be a lowercase SHA-256 digest",
    )
    return value


def _utc(value: Any, description: str) -> datetime:
    _require(isinstance(value, str), f"{description} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactVerificationError(
            f"{description} must be an ISO timestamp"
        ) from exc
    _require(
        parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0),
        f"{description} must specify UTC",
    )
    return parsed


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in obj, f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _read_json(path: Path) -> Any:
    _require(path.stat().st_size <= _MAX_JSON_BYTES,
             f"{path.name} exceeds JSON size limit")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_reject_constant(value)),
            object_pairs_hook=_no_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError(f"invalid JSON in {path.name}") from exc


def _reject_constant(value: str) -> None:
    raise ArtifactVerificationError(f"non-finite JSON constant: {value}")


def _hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _metric(actual: list[float], predicted: list[float]) -> dict[str, float]:
    count = len(actual)
    return {
        "mse": sum((a - p) ** 2 for a, p in zip(actual, predicted)) / count,
        "mae": sum(abs(a - p) for a, p in zip(actual, predicted)) / count,
        "direction_accuracy": sum(
            (a > 0) == (p > 0) for a, p in zip(actual, predicted)
        ) / count,
    }


def _compare_metrics(
    observed: Any, expected: dict[str, float], description: str
) -> None:
    metrics = _object(observed, description)
    for key, calculated in expected.items():
        saved = _number(metrics.get(key), f"{description}.{key}")
        _require(
            math.isclose(saved, calculated, rel_tol=2e-5, abs_tol=1e-12),
            f"{description}.{key} does not match saved predictions",
        )
    _require(0 <= _number(metrics["direction_accuracy"], description) <= 1,
             f"{description} direction accuracy is out of range")


def verify_run(run_dir: str | Path) -> dict[str, Any]:
    """Verify a completed E001 run without executing any checkpoint code.

    Neither the registered dataset nor the original training environment is
    available to this check; compare those separately during GPU acceptance.
    """
    directory = Path(run_dir).expanduser()
    _require(directory.is_dir() and not directory.is_symlink(),
             "run directory must be an existing, non-symlink directory")
    paths = {name: directory / name for name in (*ARTIFACT_FILES, "sha256.json")}
    for name, path in paths.items():
        _require(path.is_file() and not path.is_symlink(),
                 f"missing or unsafe artifact: {name}")

    checksums = _object(_read_json(paths["sha256.json"]), "sha256.json")
    _require(set(checksums) == set(ARTIFACT_FILES),
             "sha256.json must list exactly the four E001 artifacts")
    for name in ARTIFACT_FILES:
        expected = _digest(checksums[name], f"sha256.json[{name}]")
        _require(hmac.compare_digest(_hash_file(paths[name]), expected),
                 f"SHA-256 mismatch for {name}")

    manifest = _object(_read_json(paths["manifest.json"]), "manifest")
    metrics = _object(_read_json(paths["metrics.json"]), "metrics")
    predictions = _read_json(paths["test_predictions.json"])

    _require(manifest.get("protocol") == EXPERIMENT_PROTOCOL,
             "unsupported GPU Alpha Lab protocol")
    _require(manifest.get("research_only") is True
             and manifest.get("no_order_execution") is True,
             "artifacts must be explicitly research-only and non-executing")
    dataset_id = _digest(manifest.get("dataset_sha256"), "dataset_sha256")
    _require(isinstance(manifest.get("symbol"), str) and bool(manifest["symbol"]),
             "symbol is missing")
    timeframe = manifest.get("timeframe")
    _require(timeframe in _TIMEFRAME_SECONDS, "unsupported timeframe")
    model = manifest.get("model")
    _require(model in ("mlp", "tcn"), "unsupported model")
    horizon = _integer(manifest.get("horizon"), "horizon")
    lookback = _integer(manifest.get("lookback"), "lookback")
    _require(horizon <= 24 and 8 <= lookback <= 256,
             "unsupported feature/label window")

    train_cut = _integer(manifest.get("train_cut"), "train_cut")
    val_cut = _integer(manifest.get("validation_cut"), "validation_cut")
    _require(val_cut > train_cut, "split cuts are not chronological")
    for key in ("train_examples", "validation_examples", "test_examples"):
        _integer(manifest.get(key), key)
    _require(manifest.get("feature_names") == _FEATURE_NAMES,
             "unexpected E001 feature schema")
    for key in ("feature_mean_train_only", "feature_std_train_only"):
        values = manifest.get(key)
        _require(isinstance(values, list) and len(values) == len(_FEATURE_NAMES),
                 f"invalid {key}")
        for index, value in enumerate(values):
            number = _number(value, f"{key}[{index}]")
            if key == "feature_std_train_only":
                _require(number > 0, "normalizer std must be positive")

    train_last = _utc(manifest.get("train_final_label_at"),
                      "train_final_label_at")
    val_first = _utc(manifest.get("validation_first_feature_at"),
                     "validation_first_feature_at")
    val_last = _utc(manifest.get("validation_final_label_at"),
                    "validation_final_label_at")
    test_first = _utc(manifest.get("test_first_feature_at"),
                      "test_first_feature_at")
    test_last = _utc(manifest.get("test_final_label_at"),
                     "test_final_label_at")
    _require(train_last < val_first <= val_last < test_first <= test_last,
             "train/validation/test boundaries overlap or are reversed")

    epochs = _integer(manifest.get("epochs"), "epochs")
    best_epoch = _integer(metrics.get("best_epoch"), "best_epoch")
    _require(best_epoch <= epochs, "best_epoch exceeds requested epochs")
    _require(_number(metrics.get("validation_mse_best"),
                     "validation_mse_best") >= 0,
             "validation loss cannot be negative")
    _require(isinstance(predictions, list)
             and len(predictions) == manifest["test_examples"],
             "test prediction count differs from manifest")
    _require(bool(predictions), "test predictions are empty")

    step = timedelta(seconds=_TIMEFRAME_SECONDS[timeframe])
    expected_anchor = test_first
    actual: list[float] = []
    predicted: list[float] = []
    for index, item in enumerate(predictions):
        row = _object(item, f"prediction[{index}]")
        anchor = _utc(row.get("anchor_at"), f"prediction[{index}].anchor_at")
        target = _utc(row.get("target_at"), f"prediction[{index}].target_at")
        _require(anchor == expected_anchor,
                 f"missing, duplicated or unordered anchor at row {index}")
        _require(target == anchor + horizon * step,
                 f"prediction[{index}] target does not match horizon")
        actual.append(_number(row.get("actual_log_return"),
                              f"prediction[{index}].actual_log_return"))
        predicted.append(_number(row.get("predicted_log_return"),
                                 f"prediction[{index}].predicted_log_return"))
        expected_anchor += step
    _require(target == test_last, "last prediction target differs from manifest")

    _compare_metrics(metrics.get("test"), _metric(actual, predicted), "test")
    _compare_metrics(metrics.get("zero_return_baseline"),
                     _metric(actual, [0.0] * len(actual)),
                     "zero_return_baseline")
    return {
        "status": "verified_self_consistent",
        "protocol": EXPERIMENT_PROTOCOL,
        "dataset_sha256": dataset_id,
        "model": model,
        "test_examples": len(actual),
        "checked_artifacts": list(ARTIFACT_FILES),
        "source_dataset_reverified": False,
        "model_weights_loaded": False,
        "trading_permission": False,
        "limitations": (
            "Self-consistency only: SHA-256 does not authenticate the producer; "
            "source dataset, split reconstruction, GPU runtime and prediction "
            "validity require independent acceptance."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only offline verification of GPU Alpha Lab E001 output"
    )
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    try:
        outcome = verify_run(args.run_dir)
    except (ArtifactVerificationError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps(outcome, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
