"""First causal multi-timeframe BRLA-G2 order encoder (RESEARCH only)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .brla_g2_training_protocol import DataPurpose, OrderState, authorize_data_use
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-order-encoder-v1"
WINDOWS = {"15m": 192, "1h": 168 * 4, "4h": 180 * 16, "1d": 180 * 96}
TARGETS = ("old_order_strength", "new_order_strength", "trend_maturity",
           "remaining_potential", "entry_location_quality", "feedback_exhaustion",
           "late_entry_risk")


def _window_features(rows: list[dict[str, Any]], size: int) -> list[float]:
    window = rows[-size:]
    close = np.asarray([row["close"] for row in window], dtype=np.float64)
    volume = np.asarray([row["base_volume"] for row in window], dtype=np.float64)
    oi = np.asarray([row["open_interest"] for row in window], dtype=np.float64)
    funding = np.asarray([row["funding_rate"] for row in window], dtype=np.float64)
    depth = np.asarray([(row["bid_depth"] - row["ask_depth"])
                        / (row["bid_depth"] + row["ask_depth"]) for row in window])
    spread = np.asarray([row["spread_bps"] for row in window], dtype=np.float64)
    returns = np.diff(np.log(close))
    values = [math.log(close[-1] / close[0]), float(returns.mean()),
        float(returns.std()), math.log(volume[-1] / np.median(volume)),
        math.log(oi[-1] / oi[0]), float(funding.mean()), float(depth.mean()),
        float(spread.mean() / 100.0)]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("encoder features must be finite")
    return values


def build_examples(dataset: dict[str, Any], *, partition: Partition,
                   purpose: DataPurpose, stride_bars: int = 96
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    authorize_data_use(partition, purpose)
    if purpose not in {DataPurpose.STAGE1_TRAIN, DataPurpose.DEVELOPMENT_EVAL}:
        raise PermissionError("encoder fitting may only use TRAIN and DEVELOPMENT")
    rows_x, rows_y, rows_state = [], [], []
    required = max(WINDOWS.values())
    state_index = {state.value: index for index, state in enumerate(OrderState)}
    for path in dataset.get("paths", []):
        if path.get("partition") != partition.value:
            continue
        bars = path.get("bars", [])
        if len(bars) <= required:
            raise ValueError("path cannot satisfy the frozen 180-day causal observation")
        for end in range(required, len(bars), stride_bars):
            history = bars[:end]
            rows_x.append([value for size in WINDOWS.values()
                           for value in _window_features(history, size)])
            labels = bars[end - 1]["labels"]
            rows_y.append([float(labels[name]) for name in TARGETS])
            rows_state.append(state_index[labels["order_state"]])
    if not rows_x:
        raise ValueError("authorized partition produced no encoder examples")
    return (np.asarray(rows_x, dtype=np.float32), np.asarray(rows_y, dtype=np.float32),
            np.asarray(rows_state, dtype=np.int64))


def train_encoder(dataset: dict[str, Any], *, output: Path, seed: int = 20261111,
                  epochs: int = 30, stride_bars: int = 96) -> dict[str, Any]:
    import torch

    torch.manual_seed(seed); np.random.seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(seed)
    train_x, train_y, train_state = build_examples(dataset, partition=Partition.TRAIN,
        purpose=DataPurpose.STAGE1_TRAIN, stride_bars=stride_bars)
    dev_x, dev_y, dev_state = build_examples(dataset, partition=Partition.DEVELOPMENT,
        purpose=DataPurpose.DEVELOPMENT_EVAL, stride_bars=stride_bars)
    mean, std = train_x.mean(0), np.maximum(train_x.std(0), 1e-6)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tx = torch.tensor((train_x - mean) / std, device=device)
    ty = torch.tensor(train_y, device=device); ts = torch.tensor(train_state, device=device)
    dx = torch.tensor((dev_x - mean) / std, device=device)
    dy = torch.tensor(dev_y, device=device); ds = torch.tensor(dev_state, device=device)

    class Encoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.body = torch.nn.Sequential(torch.nn.Linear(32, 64), torch.nn.GELU(),
                torch.nn.Linear(64, 32), torch.nn.GELU())
            self.state = torch.nn.Linear(32, len(OrderState))
            self.continuous = torch.nn.Sequential(torch.nn.Linear(32, len(TARGETS)),
                                                  torch.nn.Sigmoid())
        def forward(self, value):
            encoded = self.body(value)
            return self.state(encoded), self.continuous(encoded), encoded

    model = Encoder().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    best, best_epoch, best_state, history = float("inf"), -1, None, []
    for epoch in range(epochs):
        model.train(); logits, continuous, _ = model(tx)
        train_loss = torch.nn.functional.cross_entropy(logits, ts) + \
            torch.nn.functional.mse_loss(continuous, ty)
        optimizer.zero_grad(); train_loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
        model.eval()
        with torch.no_grad():
            dev_logits, dev_continuous, _ = model(dx)
            dev_loss = (torch.nn.functional.cross_entropy(dev_logits, ds)
                        + torch.nn.functional.mse_loss(dev_continuous, dy))
            accuracy = (dev_logits.argmax(1) == ds).float().mean()
        row = {"epoch": epoch + 1, "train_loss": float(train_loss.item()),
               "development_loss": float(dev_loss.item()),
               "development_state_accuracy": float(accuracy.item())}
        history.append(row)
        if row["development_loss"] < best:
            best, best_epoch = row["development_loss"], epoch + 1
            best_state = {name: value.detach().cpu().clone()
                          for name, value in model.state_dict().items()}
    if best_state is None:
        raise RuntimeError("development selection produced no checkpoint")
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {"protocol": PROTOCOL, "state_dict": best_state,
        "normalization_mean": mean, "normalization_std": std,
        "windows": WINDOWS, "targets": TARGETS, "states": tuple(state.value for state in OrderState),
        "dataset_sha256": dataset["dataset_sha256"], "seed": seed,
        "selected_epoch": best_epoch, "mode": "RESEARCH",
        "automatic_promotion": False, "position_permission": False,
        "order_permission": False}
    torch.save(checkpoint, output)
    model_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    return {"protocol": PROTOCOL, "dataset_sha256": dataset["dataset_sha256"],
        "checkpoint_sha256": model_hash, "train_examples": len(train_x),
        "development_examples": len(dev_x), "selected_epoch": best_epoch,
        "first_train_loss": history[0]["train_loss"],
        "final_train_loss": history[-1]["train_loss"],
        "best_development_loss": best,
        "selected_development_state_accuracy": history[best_epoch - 1]["development_state_accuracy"],
        "sealed_test_examples_read": 0, "history": history,
        "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
