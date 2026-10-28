"""One-fold PyTorch walk-forward experiment, offline and research-only.

Consumes the immutable time windows from walk_forward and the per-fold
train-only normalized Samples from fold_samples. Each invocation fits one
fresh model, selects its checkpoint using validation ONLY, and evaluates
the holdout exactly once after epoch selection. No exchange/API access.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np

from .dataset import load_registered_dataset
from .fold_samples import make_fold_samples
from .train import _metrics
from .walk_forward import PROTOCOL as PLAN_PROTOCOL, describe_plan, plan_walk_forward


FOLD_TRAIN_PROTOCOL = "gpu-walk-forward-fold-v1"
_ARTIFACTS = (
    "model_weights.pt", "manifest.json", "metrics.json", "test_predictions.json",
)


def _validate_training_config(
    *, fold_number: int, model_name: str, device_name: str, seed: int,
    epochs: int, batch_size: int, learning_rate: float,
) -> None:
    if not (
        type(fold_number) is int and fold_number >= 1
        and type(seed) is int and 0 <= seed < 2**32
        and type(epochs) is int and 1 <= epochs <= 500
        and type(batch_size) is int and 1 <= batch_size <= 8192
        and isinstance(learning_rate, (int, float))
        and not isinstance(learning_rate, bool)
        and math.isfinite(learning_rate) and 0 < learning_rate <= 0.1
    ):
        raise ValueError("invalid fold number or training hyperparameters")
    if model_name not in ("mlp", "tcn") or device_name not in ("cpu", "cuda"):
        raise ValueError("model must be mlp/tcn; device must be explicit cpu/cuda")


def _save_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train_one_fold(
    *, db_path: str, dataset_id: str, output_dir: str,
    fold_number: int = 1, folds: int = 3,
    train_bars: int = 512, validation_bars: int = 128,
    test_bars: int = 128, step_bars: int | None = None,
    lookback: int = 32, horizon: int = 1,
    model_name: str = "mlp", device_name: str = "cuda",
    seed: int = 42, epochs: int = 20, batch_size: int = 64,
    learning_rate: float = 0.001,
) -> dict:
    """Fit exactly one explicitly chosen fold and seal its research artifacts.

    Read-only source DB; new output directory required. sha256.json is
    written LAST as the completion marker: an interrupted output without
    this file is invalid and must never be considered a completed run.
    """
    _validate_training_config(
        fold_number=fold_number, model_name=model_name, device_name=device_name,
        seed=seed, epochs=epochs, batch_size=batch_size,
        learning_rate=learning_rate,
    )
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError("output directory already exists; use a fresh run ID")

    dataset = load_registered_dataset(db_path, dataset_id)
    windows = plan_walk_forward(
        len(dataset.times), folds=folds, train_bars=train_bars,
        validation_bars=validation_bars, test_bars=test_bars,
        step_bars=step_bars, lookback=lookback, horizon=horizon,
    )
    if fold_number > len(windows):
        raise ValueError("fold_number exceeds the frozen plan's fold count")
    plan = describe_plan(
        dataset, folds=folds, train_bars=train_bars,
        validation_bars=validation_bars, test_bars=test_bars,
        step_bars=step_bars, lookback=lookback, horizon=horizon,
    )
    fold = windows[fold_number - 1]
    split = make_fold_samples(dataset, fold, lookback=lookback, horizon=horizon)

    # Must be set before importing torch when CUDA deterministic GEMM is used.
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch
    from .models import make_model

    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA explicitly requested but unavailable (no CPU fallback)")
    device = torch.device(device_name)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)

    model = make_model(
        model_name, lookback=lookback, features=split.train_x.shape[2]
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=learning_rate, weight_decay=1e-4,
    )
    loss_function = torch.nn.MSELoss()
    train_x = torch.from_numpy(split.train_x)
    train_y = torch.from_numpy(split.train_y)
    validation_x = torch.from_numpy(split.val_x).to(device)
    validation_y = torch.from_numpy(split.val_y).to(device)

    best_epoch = 0
    best_loss = float("inf")
    best_state = None
    for epoch in range(epochs):
        model.train()
        order = torch.randperm(
            len(train_x),
            generator=torch.Generator().manual_seed(seed + epoch),
        )
        for indices in order.split(batch_size):
            x = train_x[indices].to(device)
            y = train_y[indices].to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(x), y)
            if not torch.isfinite(loss):
                raise RuntimeError("non-finite training loss")
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            validation_loss = float(
                loss_function(model(validation_x), validation_y).item()
            )
        if not math.isfinite(validation_loss):
            raise RuntimeError("non-finite validation loss")
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_epoch = epoch + 1
            best_state = copy.deepcopy(model.state_dict())

    if best_state is None:
        raise RuntimeError("training did not yield a valid validation checkpoint")
    model.load_state_dict(best_state)
    model.eval()

    # Important: holdout labels/metrics are first consulted AFTER checkpoint
    # selection. Never use holdout loss to change model/epoch/hyperparameters.
    with torch.no_grad():
        predictions = (
            model(torch.from_numpy(split.test_x).to(device)).cpu().numpy()
        )
    if not np.isfinite(predictions).all():
        raise RuntimeError("non-finite holdout predictions")
    actual = split.test_y
    metrics = {
        "best_epoch": best_epoch,
        "validation_mse_best": best_loss,
        "test": _metrics(actual, predictions),
        "zero_return_baseline": _metrics(actual, np.zeros_like(actual)),
    }
    manifest = {
        "protocol": FOLD_TRAIN_PROTOCOL,
        "plan_protocol": PLAN_PROTOCOL,
        "plan_sha256": plan["plan_sha256"],
        "dataset_sha256": dataset.dataset_id,
        "symbol": dataset.symbol,
        "timeframe": dataset.timeframe,
        "fold_number": fold_number,
        "folds_planned": len(windows),
        "plan_configuration": plan["configuration"],
        "window": plan["windows"][fold_number - 1],
        "model": model_name,
        "device": str(device),
        "gpu_name": (
            torch.cuda.get_device_name(device) if device.type == "cuda"
            else None
        ),
        "torch_version": str(torch.__version__),
        "python_version": sys.version.split()[0],
        "seed": seed,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "lookback": lookback,
        "horizon": horizon,
        "train_examples": int(len(split.train_y)),
        "validation_examples": int(len(split.val_y)),
        "test_examples": int(len(split.test_y)),
        "feature_names": [
            "log_return", "log_high_low", "log_close_open",
            "log1p_volume_change",
        ],
        "feature_mean_train_only": split.feature_mean.tolist(),
        "feature_std_train_only": split.feature_std.tolist(),
        "checkpoint_selection": "validation_mse_only",
        "holdout_evaluations": 1,
        "research_only": True,
        "no_order_execution": True,
        "automatic_strategy_promotion": False,
    }
    prediction_rows = [
        {
            "anchor_at": dataset.times[int(index)],
            "target_at": dataset.times[int(index) + horizon],
            "actual_log_return": float(y),
            "predicted_log_return": float(pred),
        }
        for index, y, pred in zip(split.test_anchors, actual, predictions)
    ]

    # Never overwrite an earlier run, including interrupted run directories.
    # Completion marker is emitted only after every other artifact is written.
    target.mkdir(parents=True, exist_ok=False)
    torch.save(best_state, target / "model_weights.pt")
    _save_json(target / "manifest.json", manifest)
    _save_json(target / "metrics.json", metrics)
    _save_json(target / "test_predictions.json", prediction_rows)
    checksums = {name: _sha256_file(target / name) for name in _ARTIFACTS}
    _save_json(target / "sha256.json", checksums)
    return {
        "output_dir": str(target),
        "fold_number": fold_number,
        "plan_sha256": plan["plan_sha256"],
        "metrics": metrics,
        "device": str(device),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One explicit, research-only GPU Walk-forward fold"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--fold", type=int, required=True)
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
    result = train_one_fold(
        db_path=args.db_path, dataset_id=args.dataset_id,
        output_dir=args.output_dir, fold_number=args.fold,
        folds=args.folds, train_bars=args.train_bars,
        validation_bars=args.validation_bars,
        test_bars=args.test_bars, step_bars=args.step_bars,
        lookback=args.lookback, horizon=args.horizon,
        model_name=args.model, device_name=args.device,
        seed=args.seed, epochs=args.epochs, batch_size=args.batch_size,
        learning_rate=args.learning_rate,
    )
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
