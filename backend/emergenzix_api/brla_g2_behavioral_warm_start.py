"""Audits for the masked BRLA-G2 behavioral warm-start policy."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

import numpy as np

from .brla_g2_ppo_env import INTENTS

PROTOCOL = "emergenzix-brla-g2-behavioral-warm-start-v1"


def classification_audit(labels: Sequence[int], predictions: Sequence[int],
                         masks: Sequence[Sequence[bool]]) -> dict[str, object]:
    if not labels or len(labels) != len(predictions) or len(labels) != len(masks):
        raise ValueError("labels, predictions and masks must have the same non-zero length")
    label_counts = Counter(int(value) for value in labels)
    prediction_counts = Counter(int(value) for value in predictions)
    recalls = {}
    for action, count in sorted(label_counts.items()):
        correct = sum(int(label) == action and int(prediction) == action
                      for label, prediction in zip(labels, predictions))
        recalls[INTENTS[action]] = correct / count
    illegal = sum(not bool(mask[int(prediction)])
                  for prediction, mask in zip(predictions, masks))
    return {
        "example_count": len(labels),
        "accuracy": float(np.mean(np.asarray(labels) == np.asarray(predictions))),
        "macro_recall": float(np.mean(list(recalls.values()))),
        "recall_by_intent": recalls,
        "label_counts": {INTENTS[key]: value for key, value in label_counts.items()},
        "prediction_counts": {INTENTS[key]: value for key, value in prediction_counts.items()},
        "predicted_action_diversity": len(prediction_counts),
        "predicted_probe_fraction": prediction_counts[INTENTS.index("PROBE_SHORT")] / len(labels),
        "illegal_prediction_count": illegal,
    }
