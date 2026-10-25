"""Experiment 001: offline PyTorch return regression with immutable provenance.

Never accesses exchange APIs, paper-order routes or trade credentials.
Validation chooses the checkpoint; holdout/test is consulted exactly once.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import numpy as np

from . import EXPERIMENT_PROTOCOL
from .dataset import load_registered_dataset, make_samples


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "mse": float(np.mean((actual - predicted) ** 2)),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "direction_accuracy": float(np.mean((actual > 0) == (predicted > 0))),
    }


def train_experiment(
    *, db_path: str, dataset_id: str, output_dir: str,
    model_name: str = "mlp", device_name: str = "auto", seed: int = 42,
    epochs: int = 20, batch_size: int = 64, learning_rate: float = 0.001,
    lookback: int = 32, horizon: int = 1,
) -> dict:
    if not (0 <= seed < 2**32 and 1 <= epochs <= 500 and
            1 <= batch_size <= 8192 and 0 < learning_rate <= 0.1):
        raise ValueError("invalid training hyperparameters")
    if model_name not in ("mlp", "tcn") or device_name not in ("auto", "cpu", "cuda"):
        raise ValueError("unknown model or device")
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError("output directory already exists; choose a unique run ID")

    data = load_registered_dataset(db_path, dataset_id)
    split = make_samples(data, lookback=lookback, horizon=horizon)
    # Set this *before* importing torch to constrain deterministic CUDA GEMMs.
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import torch
    from .models import make_model

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA explicitly requested but unavailable (no CPU fallback)")
    device = torch.device(
        "cuda" if device_name != "cpu" and torch.cuda.is_available() else "cpu"
    )
    model = make_model(
        model_name, lookback=lookback, features=split.train_x.shape[2]
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate,
                                  weight_decay=1e-4)
    loss_function = torch.nn.MSELoss()
    train_x, train_y = (
        torch.from_numpy(split.train_x), torch.from_numpy(split.train_y)
    )
    validation_x = torch.from_numpy(split.val_x).to(device)
    validation_y = torch.from_numpy(split.val_y).to(device)

    best_loss = float("inf")
    best_state = None
    best_epoch = 0
    for epoch in range(epochs):
        model.train()
        permutation = torch.randperm(
            len(train_x), generator=torch.Generator().manual_seed(seed + epoch)
        )
        for indices in permutation.split(batch_size):
            x, y = train_x[indices].to(device), train_y[indices].to(device)
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
        if not np.isfinite(validation_loss):
            raise RuntimeError("non-finite validation loss")
        if validation_loss < best_loss:
            best_loss, best_epoch = validation_loss, epoch + 1
            best_state = copy.deepcopy(model.state_dict())
    assert best_state is not None
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        predictions = model(torch.from_numpy(split.test_x).to(device)).cpu().numpy()
    if not np.isfinite(predictions).all():
        raise RuntimeError("non-finite holdout predictions")
    actual = split.test_y
    metrics = {
        "test": _metrics(actual, predictions),
        "zero_return_baseline": _metrics(actual, np.zeros_like(actual)),
        "validation_mse_best": best_loss,
        "best_epoch": best_epoch,
    }

    manifest = {
        "protocol": EXPERIMENT_PROTOCOL,
        "dataset_sha256": data.dataset_id,
        "symbol": data.symbol,
        "timeframe": data.timeframe,
        "model": model_name,
        "seed": seed,
        "lookback": lookback,
        "horizon": horizon,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "train_cut": split.train_cut,
        "validation_cut": split.val_cut,
        "train_examples": int(len(split.train_y)),
        "validation_examples": int(len(split.val_y)),
        "test_examples": int(len(split.test_y)),
        "train_final_label_at": data.times[int(split.train_anchors[-1]) + horizon],
        "validation_first_feature_at": data.times[int(split.val_anchors[0])],
        "validation_final_label_at": data.times[int(split.val_anchors[-1]) + horizon],
        "test_first_feature_at": data.times[int(split.test_anchors[0])],
        "test_final_label_at": data.times[int(split.test_anchors[-1]) + horizon],
        "feature_names": [
            "log_return", "log_high_low", "log_close_open",
            "log1p_volume_change",
        ],
        "feature_mean_train_only": split.feature_mean.tolist(),
        "feature_std_train_only": split.feature_std.tolist(),
        "torch_version": str(torch.__version__),
        "python_version": sys.version.split()[0],
        "device": str(device),
        "gpu_name": (
            torch.cuda.get_device_name(0) if device.type == "cuda" else None
        ),
        "research_only": True,
        "no_order_execution": True,
    }

    # Never overwrite an earlier experiment, and never write to the source DB.
    target.mkdir(parents=True, exist_ok=False)
    torch.save(best_state, target / "model_weights.pt")
    for name, content in (("manifest.json", manifest), ("metrics.json", metrics)):
        (target / name).write_text(
            json.dumps(content, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    prediction_rows = [
        {
            "anchor_at": data.times[int(anchor)],
            "target_at": data.times[int(anchor) + horizon],
            "actual_log_return": float(y),
            "predicted_log_return": float(pred),
        }
        for anchor, y, pred in zip(split.test_anchors, actual, predictions)
    ]
    (target / "test_predictions.json").write_text(
        json.dumps(prediction_rows, indent=2) + "\n", encoding="utf-8"
    )
    checksums = {
        filename: hashlib.sha256((target / filename).read_bytes()).hexdigest()
        for filename in (
            "model_weights.pt", "manifest.json", "metrics.json",
            "test_predictions.json",
        )
    }
    (target / "sha256.json").write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"output_dir": str(target), "metrics": metrics, "device": str(device)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline, research-only GPU Alpha Experiment 001"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model", choices=("mlp", "tcn"), default="mlp")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="cuda")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--lookback", type=int, default=32)
    parser.add_argument("--horizon", type=int, default=1)
    args = parser.parse_args()
    result = train_experiment(
        db_path=args.db_path,
        dataset_id=args.dataset_id,
        output_dir=args.output_dir,
        model_name=args.model,
        device_name=args.device,
        seed=args.seed,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        lookback=args.lookback,
        horizon=args.horizon,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
