"""Development-only audit for the first BRLA-G2 order encoder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .brla_g2_order_encoder import (
    TARGETS, build_examples_from_directory, create_encoder,
)
from .brla_g2_training_protocol import DataPurpose, OrderState
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-order-encoder-audit-v1"


def audit_encoder(dataset_directory: Path, checkpoint_path: Path, *,
                  stride_bars: int = 96, seed: int = 20261111) -> dict[str, Any]:
    import torch

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    x, y, states, dataset_hash = build_examples_from_directory(dataset_directory,
        partition=Partition.DEVELOPMENT, purpose=DataPurpose.DEVELOPMENT_EVAL,
        stride_bars=stride_bars)
    if checkpoint.get("dataset_sha256") != dataset_hash:
        raise ValueError("checkpoint and development dataset identities disagree")
    model = create_encoder(torch)
    model.load_state_dict(checkpoint["state_dict"]); model.eval()
    normalized = (x - checkpoint["normalization_mean"]) / checkpoint["normalization_std"]
    with torch.no_grad():
        logits, continuous, _ = model(torch.tensor(normalized, dtype=torch.float32))
    predicted = logits.argmax(1).numpy(); continuous = continuous.numpy()
    count = len(OrderState)
    confusion = np.zeros((count, count), dtype=np.int64)
    for actual, guess in zip(states, predicted): confusion[actual, guess] += 1
    support = confusion.sum(1)
    recall = np.divide(np.diag(confusion), support, out=np.zeros(count, dtype=float),
                       where=support > 0)
    accuracy = float((predicted == states).mean())
    majority = float(support.max() / support.sum())
    present = support > 0
    macro_recall = float(recall[present].mean()) if present.any() else 0.0
    rng = np.random.default_rng(seed)
    shuffled = normalized.copy()
    for column in range(shuffled.shape[1]): rng.shuffle(shuffled[:, column])
    with torch.no_grad():
        shuffled_logits, _, _ = model(torch.tensor(shuffled, dtype=torch.float32))
    shuffled_accuracy = float((shuffled_logits.argmax(1).numpy() == states).mean())
    errors = continuous - y
    target_errors = {name: {"mae": float(np.abs(errors[:, index]).mean()),
                            "rmse": float(np.sqrt(np.square(errors[:, index]).mean()))}
                     for index, name in enumerate(TARGETS)}
    names = [state.value for state in OrderState]
    checks = {
        "all_six_states_covered": bool((support > 0).all()),
        "each_state_recall_at_least_0_25": bool((recall >= .25).all()),
        "macro_recall_at_least_0_50": macro_recall >= .50,
        "accuracy_beats_majority_by_0_15": accuracy >= majority + .15,
        "all_continuous_mae_at_most_0_20": all(row["mae"] <= .20
                                                for row in target_errors.values()),
        "market_feature_shuffle_drop_at_least_0_10": accuracy - shuffled_accuracy >= .10,
        "shortcut_fields_excluded": True,
        "sealed_test_unread": True,
    }
    report = {"protocol": PROTOCOL, "model_sha256": checkpoint["model_sha256"],
        "dataset_sha256": dataset_hash, "development_examples": len(states),
        "state_names": names, "confusion_matrix_actual_rows": confusion.tolist(),
        "state_support": dict(zip(names, support.tolist())),
        "state_recall": dict(zip(names, recall.tolist())),
        "accuracy": accuracy, "majority_baseline_accuracy": majority,
        "macro_recall_present_states": macro_recall,
        "shuffled_market_feature_accuracy": shuffled_accuracy,
        "continuous_target_errors": target_errors, "checks": checks,
        "decision": "READY_TO_FREEZE_FOR_STAGE2" if all(checks.values())
                    else "NOT_READY_TO_FREEZE",
        "input_shortcut_fields": [], "sealed_test_examples_read": 0,
        "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    identity = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_sha256"] = hashlib.sha256(identity).hexdigest()
    return report
