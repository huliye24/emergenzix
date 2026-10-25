"""Sequential, fail-closed Walk-forward batch runner (research only).

A frozen, verified source dataset determines every fold before fitting any
model. Each fold runs exactly once, is independently source-verified, and
only then permits the next fold. An atomic batch completion marker is
written only after *all* scheduled folds pass verification.

This module never connects to an exchange, runs PAPER orders, or promotes
models. Interrupted runs are NOT resumed or overwritten by this protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .dataset import load_registered_dataset
from .fold_train import _validate_training_config, train_one_fold
from .fold_verify import verify_fold_run
from .walk_forward import describe_plan


BATCH_PROTOCOL = "gpu-walk-forward-batch-v1"


class BatchRunError(ValueError):
    """A fold batch cannot be considered completed safely."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BatchRunError(message)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, indent=2,
            ensure_ascii=False, allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_new_json(path: Path, value: object) -> None:
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _write_completion_marker(path: Path, value: object) -> None:
    """Atomically publish the marker, never overwrite a previous completion."""
    temporary = path.with_name(".batch-complete.tmp")
    _write_new_json(temporary, value)
    # The parent was just created exclusively by this invocation.
    _require(not path.exists() and not path.is_symlink(),
             "batch completion marker unexpectedly exists")
    os.replace(temporary, path)


def run_walk_forward_batch(
    *, db_path: str, dataset_id: str, output_dir: str,
    folds: int = 3, train_bars: int = 512, validation_bars: int = 128,
    test_bars: int = 128, step_bars: int | None = None,
    lookback: int = 32, horizon: int = 1,
    model_name: str = "mlp", device_name: str = "cuda",
    seed: int = 42, epochs: int = 20, batch_size: int = 64,
    learning_rate: float = 0.001,
) -> dict[str, Any]:
    """Execute and source-verify every planned fold in chronological order.

    Reusing one frozen seed across the folds is intentional: each fold
    starts from an independently initialized model, not a warmed-up
    previous checkpoint. The same configuration is passed to each fold.
    This is not a batch-level strategy selection or optimization loop.
    """
    _validate_training_config(
        fold_number=1, model_name=model_name, device_name=device_name,
        seed=seed, epochs=epochs, batch_size=batch_size,
        learning_rate=learning_rate,
    )
    target = Path(output_dir).expanduser()
    if target.exists() or target.is_symlink():
        raise FileExistsError("batch output directory already exists; choose a new ID")

    # Both the verified source and complete, bounded plan must exist BEFORE
    # touching the output directory or invoking any fold trainer.
    dataset = load_registered_dataset(db_path, dataset_id)
    plan = describe_plan(
        dataset, folds=folds, train_bars=train_bars,
        validation_bars=validation_bars, test_bars=test_bars,
        step_bars=step_bars, lookback=lookback, horizon=horizon,
    )
    configuration = {
        "model": model_name,
        "device": device_name,
        "seed": seed,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
    }
    batch_plan = {
        "protocol": BATCH_PROTOCOL,
        "dataset_sha256": dataset.dataset_id,
        "plan": plan,
        "training": configuration,
        "execution_order": list(range(1, len(plan["windows"]) + 1)),
        "research_only": True,
        "no_order_execution": True,
        "automatic_strategy_promotion": False,
        "batch_completion_required": True,
    }

    # Never overwrite/restart an existing batch. An interrupted batch remains
    # inspectable on disk but has no batch_complete.json success marker.
    target.mkdir(parents=True, exist_ok=False)
    _write_new_json(target / "batch_plan.json", batch_plan)
    batch_plan_sha = _sha256(target / "batch_plan.json")

    verified: list[dict[str, Any]] = []
    for fold_index in batch_plan["execution_order"]:
        directory_name = f"fold-{fold_index:03d}"
        fold_dir = target / directory_name

        outcome = train_one_fold(
            db_path=db_path, dataset_id=dataset.dataset_id,
            output_dir=str(fold_dir), fold_number=fold_index,
            folds=plan["configuration"]["folds"],
            train_bars=train_bars, validation_bars=validation_bars,
            test_bars=test_bars, step_bars=step_bars,
            lookback=lookback, horizon=horizon,
            model_name=model_name, device_name=device_name,
            seed=seed, epochs=epochs, batch_size=batch_size,
            learning_rate=learning_rate,
        )
        _require(isinstance(outcome, dict),
                 f"fold {fold_index} returned no training result")
        _require(
            outcome.get("fold_number") == fold_index
            and outcome.get("plan_sha256") == plan["plan_sha256"]
            and outcome.get("device") == device_name
            and outcome.get("output_dir") == str(fold_dir),
            f"fold {fold_index} training provenance mismatch",
        )

        evidence = verify_fold_run(fold_dir, db_path=db_path)
        _require(isinstance(evidence, dict),
                 f"fold {fold_index} returned no verification evidence")
        _require(
            evidence.get("status") == "verified_against_source"
            and evidence.get("fold_number") == fold_index
            and evidence.get("dataset_sha256") == dataset.dataset_id
            and evidence.get("plan_sha256") == plan["plan_sha256"]
            and evidence.get("model") == model_name
            and evidence.get("source_dataset_reverified") is True
            and evidence.get("source_plan_reconstructed") is True
            and evidence.get("model_weights_loaded") is False
            and evidence.get("trading_permission") is False
            and type(evidence.get("test_examples")) is int
            and evidence["test_examples"]
                == plan["windows"][fold_index - 1]["test"]["sample_count"],
            f"fold {fold_index} source verification failed or disagrees",
        )

        completion = fold_dir / "sha256.json"
        _require(fold_dir.is_dir() and not fold_dir.is_symlink()
                 and completion.is_file() and not completion.is_symlink(),
                 f"fold {fold_index} has no safe completion marker")
        verified.append({
            "fold_number": fold_index,
            "relative_directory": directory_name,
            "fold_sha256_json_sha256": _sha256(completion),
            "test_examples": evidence["test_examples"],
            "verification_status": evidence["status"],
        })

    _require(len(verified) == len(plan["windows"]),
             "not all planned folds were independently verified")
    complete = {
        "protocol": BATCH_PROTOCOL,
        "status": "completed_verified_folds",
        "dataset_sha256": dataset.dataset_id,
        "plan_sha256": plan["plan_sha256"],
        "batch_plan_file_sha256": batch_plan_sha,
        "folds_planned": len(plan["windows"]),
        "folds_verified": len(verified),
        "fold_results": verified,
        "research_only": True,
        "no_order_execution": True,
        "automatic_strategy_promotion": False,
        "trading_permission": False,
        "limitations": (
            "Each fold is source-verified, not independently replayed "
            "from saved PyTorch weights. No multi-fold statistical "
            "assessment, fees, slippage, PnL or trading authorization."
        ),
    }
    _write_completion_marker(target / "batch_complete.json", complete)
    return {
        "output_dir": str(target),
        "status": complete["status"],
        "plan_sha256": plan["plan_sha256"],
        "folds_verified": len(verified),
        "trading_permission": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sequential verified GPU Walk-forward batch (research only)"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--train-bars", type=int, default=512)
    parser.add_argument("--validation-bars", type=int, default=128)
    parser.add_argument("--test-bars", type=int, default=128)
    parser.add_argument("--step-bars", type=int)
    parser.add_argument("--lookback", type=int, default=32)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--model", choices=("mlp", "tcn"), default="mlp")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    args = parser.parse_args()

    result = run_walk_forward_batch(
        db_path=args.db_path, dataset_id=args.dataset_id,
        output_dir=args.output_dir, folds=args.folds,
        train_bars=args.train_bars, validation_bars=args.validation_bars,
        test_bars=args.test_bars, step_bars=args.step_bars,
        lookback=args.lookback, horizon=args.horizon,
        model_name=args.model, device_name=args.device,
        seed=args.seed, epochs=args.epochs, batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
