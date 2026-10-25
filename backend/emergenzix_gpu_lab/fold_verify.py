"""Read-only verification of completed GPU walk-forward *fold* artifacts.

Reopens the registered immutable OHLCV dataset via the existing SHA-256
verifier and rebuilds the exact frozen plan. Does not import PyTorch, load
model_weights.pt, execute trades, or trust a manifest-provided plan digest.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .dataset import causal_ohlcv_features, load_registered_dataset
from .walk_forward import PROTOCOL as PLAN_PROTOCOL, describe_plan


FOLD_PROTOCOL = "gpu-walk-forward-fold-v1"
ARTIFACT_FILES = (
    "model_weights.pt", "manifest.json", "metrics.json", "test_predictions.json",
)
FEATURE_NAMES = (
    "log_return", "log_high_low", "log_close_open", "log1p_volume_change",
)
MAX_JSON_BYTES = 32 * 1024 * 1024


class FoldVerificationError(ValueError):
    """A saved fold cannot be accepted as internally consistent evidence."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FoldVerificationError(reason)


def _obj(value: Any, field: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{field} must be a JSON object")
    return value


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum,
             f"{field} must be an integer >= {minimum}")
    return value


def _finite(value: Any, field: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(float(value)),
             f"{field} must be a finite number")
    return float(value)


def _sha(value: Any, field: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64
        and all(c in "0123456789abcdef" for c in value),
        f"{field} must be lowercase SHA-256",
    )
    return value


def _reject_constant(value: str) -> None:
    raise FoldVerificationError(f"nonfinite JSON constant: {value}")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    _require(path.stat().st_size <= MAX_JSON_BYTES,
             f"artifact too large: {path.name}")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, FoldVerificationError):
            raise
        raise FoldVerificationError(f"invalid JSON: {path.name}") from exc


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _close(actual: Any, expected: float, field: str,
           *, rel_tol: float = 1e-6, abs_tol: float = 1e-12) -> None:
    saved = _finite(actual, field)
    _require(math.isfinite(expected) and math.isclose(
        saved, expected, rel_tol=rel_tol, abs_tol=abs_tol,
    ), f"{field} disagrees with independently recomputed value")


def _score(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "mse": float(np.mean((actual - predicted) ** 2)),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "direction_accuracy": float(np.mean((actual > 0) == (predicted > 0))),
    }


def _check_metrics(saved: Any, expected: dict[str, float], field: str) -> None:
    obj = _obj(saved, field)
    _require(set(obj) == set(expected),
             f"{field} contains missing or unsupported metrics")
    for name, value in expected.items():
        _close(obj[name], value, f"{field}.{name}")
    _require(0 <= _finite(obj["direction_accuracy"], field) <= 1,
             f"{field} direction_accuracy outside 0..1")


def verify_fold_run(
    run_dir: str | Path, *, db_path: str | Path,
) -> dict[str, Any]:
    """Recheck saved one-fold evidence against registered, verified OHLCV.

    Requires the source database, unlike a mere hash-only check: the plan
    digest, fold boundaries, holdout target values, and training scaler
    are reconstructed from the original content-addressed candle data.

    Checkpoint bytes are hashed but NEVER deserialized. This does not
    reproduce training, verify the model architecture or prove profit.
    """
    root = Path(run_dir).expanduser()
    _require(root.is_dir() and not root.is_symlink(),
             "run directory missing or symlinked")
    files = {name: root / name for name in (*ARTIFACT_FILES, "sha256.json")}
    for name, path in files.items():
        _require(path.is_file() and not path.is_symlink(),
                 f"missing, incomplete or unsafe artifact: {name}")
    _require(files["model_weights.pt"].stat().st_size > 0,
             "model checkpoint must not be empty")

    checksums = _obj(_read_json(files["sha256.json"]), "sha256.json")
    _require(set(checksums) == set(ARTIFACT_FILES),
             "completion marker must name exactly four artifacts")
    for name in ARTIFACT_FILES:
        expected_hash = _sha(checksums[name], f"sha256[{name}]")
        _require(hmac.compare_digest(_file_sha(files[name]), expected_hash),
                 f"SHA-256 mismatch: {name}")

    manifest = _obj(_read_json(files["manifest.json"]), "manifest")
    saved_metrics = _obj(_read_json(files["metrics.json"]), "metrics")
    predictions = _read_json(files["test_predictions.json"])

    _require(manifest.get("protocol") == FOLD_PROTOCOL,
             "unsupported fold experiment protocol")
    _require(manifest.get("plan_protocol") == PLAN_PROTOCOL,
             "unsupported plan protocol")
    for name, expected in (
        ("research_only", True),
        ("no_order_execution", True),
        ("automatic_strategy_promotion", False),
        ("checkpoint_selection", "validation_mse_only"),
        ("holdout_evaluations", 1),
    ):
        _require(type(manifest.get(name)) is type(expected)
                 and manifest.get(name) == expected,
                 f"unsafe or unsupported manifest field: {name}")

    dataset_sha = _sha(manifest.get("dataset_sha256"), "dataset_sha256")
    plan_sha = _sha(manifest.get("plan_sha256"), "plan_sha256")
    dataset = load_registered_dataset(db_path, dataset_sha)
    _require(manifest.get("symbol") == dataset.symbol
             and manifest.get("timeframe") == dataset.timeframe,
             "manifest disagrees with verified source dataset")

    cfg = _obj(manifest.get("plan_configuration"), "plan_configuration")
    required = {
        "folds", "train_bars_initial", "validation_bars", "test_bars",
        "step_bars", "lookback", "horizon",
    }
    _require(set(cfg) == required, "unexpected plan configuration schema")
    for key in required:
        _integer(cfg[key], f"plan_configuration.{key}", 1)
    plan = describe_plan(
        dataset, folds=cfg["folds"], train_bars=cfg["train_bars_initial"],
        validation_bars=cfg["validation_bars"],
        test_bars=cfg["test_bars"], step_bars=cfg["step_bars"],
        lookback=cfg["lookback"], horizon=cfg["horizon"],
    )
    _require(hmac.compare_digest(plan_sha, plan["plan_sha256"]),
             "plan_sha256 disagrees with independently reconstructed plan")
    _require(cfg == plan["configuration"],
             "manifest plan configuration does not match source")
    fold_number = _integer(manifest.get("fold_number"), "fold_number", 1)
    _require(fold_number <= len(plan["windows"]), "fold number outside plan")
    _require(_integer(manifest.get("folds_planned"), "folds_planned", 1)
             == len(plan["windows"]), "fold count disagrees with plan")
    window = plan["windows"][fold_number - 1]
    _require(manifest.get("window") == window,
             "fold boundaries/timestamps disagree with verified plan")
    for name in ("lookback", "horizon"):
        _require(_integer(manifest.get(name), name, 1) == cfg[name],
                 f"{name} disagrees with frozen plan")
    for name, stage in (
        ("train_examples", "train"),
        ("validation_examples", "validation"),
        ("test_examples", "test"),
    ):
        _require(_integer(manifest.get(name), name, 1)
                 == window[stage]["sample_count"],
                 f"{name} disagrees with planned purged window")

    _require(manifest.get("model") in ("mlp", "tcn"), "unknown model")
    _require(manifest.get("device") in ("cpu", "cuda"), "unknown device")
    _integer(manifest.get("seed"), "seed")
    epochs = _integer(manifest.get("epochs"), "epochs", 1)
    _require(epochs <= 500, "epochs exceeds training protocol")
    _require(1 <= _integer(manifest.get("batch_size"), "batch_size", 1) <= 8192,
             "batch_size outside protocol")
    learning_rate = _finite(manifest.get("learning_rate"), "learning_rate")
    _require(0 < learning_rate <= 0.1, "learning_rate outside protocol")
    _require(manifest.get("feature_names") == list(FEATURE_NAMES),
             "unexpected E001 feature schema")

    # Refit scaler on features no later than the last training anchor.
    features = causal_ohlcv_features(dataset)
    train_stop = window["train"]["anchors"][1]
    train_features = features[:train_stop]
    expected_mean = train_features.mean(axis=0, dtype=np.float64)
    expected_std = np.maximum(train_features.std(axis=0, dtype=np.float64), 1e-8)
    for field, expected in (
        ("feature_mean_train_only", expected_mean),
        ("feature_std_train_only", expected_std),
    ):
        values = manifest.get(field)
        _require(isinstance(values, list) and len(values) == len(FEATURE_NAMES),
                 f"{field} has invalid dimensions")
        for i, (saved, value) in enumerate(zip(values, expected)):
            _close(saved, float(value), f"{field}[{i}]",
                   rel_tol=1e-12, abs_tol=1e-13)
            if field == "feature_std_train_only":
                _require(_finite(saved, f"{field}[{i}]") > 0,
                         "train-only standard deviation must be positive")

    test = window["test"]
    first_anchor, last_exclusive = test["anchors"]
    horizon = cfg["horizon"]
    _require(isinstance(predictions, list)
             and len(predictions) == last_exclusive - first_anchor,
             "incomplete or extra holdout predictions")
    actual: list[float] = []
    predicted: list[float] = []
    for offset, row in enumerate(predictions):
        index = first_anchor + offset
        entry = _obj(row, f"test_predictions[{offset}]")
        _require(
            set(entry) == {
                "anchor_at", "target_at", "actual_log_return",
                "predicted_log_return",
            },
            f"invalid prediction fields at row {offset}",
        )
        _require(entry["anchor_at"] == dataset.times[index]
                 and entry["target_at"] == dataset.times[index + horizon],
                 f"incorrect or reordered holdout timestamp at row {offset}")
        # Trainer writes np.float32 target labels, not double-precision labels.
        expected_label = float(np.float32(np.log(
            dataset.close[index + horizon] / dataset.close[index]
        )))
        observed_label = _finite(entry["actual_log_return"],
                                 f"actual_log_return[{offset}]")
        _require(observed_label == expected_label,
                 f"holdout label disagrees with verified source at row {offset}")
        actual.append(observed_label)
        predicted.append(_finite(entry["predicted_log_return"],
                                 f"predicted_log_return[{offset}]"))

    y = np.asarray(actual, dtype=np.float32)
    p = np.asarray(predicted, dtype=np.float32)
    _require(np.isfinite(y).all() and np.isfinite(p).all(),
             "holdout predictions contain invalid floats")
    _require(set(saved_metrics) == {
        "best_epoch", "validation_mse_best", "test", "zero_return_baseline"
    }, "unsupported metrics schema")
    best_epoch = _integer(saved_metrics["best_epoch"], "best_epoch", 1)
    _require(best_epoch <= epochs, "best_epoch exceeds training epochs")
    _require(_finite(saved_metrics["validation_mse_best"],
                     "validation_mse_best") >= 0, "negative validation MSE")
    _check_metrics(saved_metrics["test"], _score(y, p), "test")
    _check_metrics(
        saved_metrics["zero_return_baseline"],
        _score(y, np.zeros_like(y)),
        "zero_return_baseline",
    )
    return {
        "status": "verified_against_source",
        "protocol": FOLD_PROTOCOL,
        "dataset_sha256": dataset_sha,
        "plan_sha256": plan_sha,
        "fold_number": fold_number,
        "model": manifest["model"],
        "test_examples": len(predictions),
        "source_dataset_reverified": True,
        "source_plan_reconstructed": True,
        "model_weights_loaded": False,
        "predictions_regenerated_from_model": False,
        "trading_permission": False,
        "limitations": (
            "Content hashes and source checks are not producer signatures. "
            "Saved model predictions were not regenerated from model weights; "
            "training quality and future trading profitability are unproven."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only, CPU-only fold artifact verification"
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    try:
        report = verify_fold_run(args.run_dir, db_path=args.db_path)
    except (FoldVerificationError, ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        sys.exit(1)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
