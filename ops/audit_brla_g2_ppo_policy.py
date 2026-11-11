#!/usr/bin/env python3
"""Audit action probabilities of a frozen PPO policy without training it."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from sb3_contrib import MaskablePPO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_policy_audit import (  # noqa: E402
    PROTOCOL, summarize_flat_probabilities,
)
from emergenzix_api.brla_g2_ppo_env import (  # noqa: E402
    BRLAG2PPOEnv, INTENTS, prepare_episodes,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def action_probabilities(model: MaskablePPO, observation: np.ndarray,
                         masks: np.ndarray) -> np.ndarray:
    tensor, _ = model.policy.obs_to_tensor(observation)
    distribution = model.policy.get_distribution(tensor, action_masks=masks)
    return distribution.distribution.probs.detach().cpu().numpy()[0]


def audit_partition(model: MaskablePPO, episodes) -> dict[str, object]:
    rows = []
    for episode in episodes:
        env = BRLAG2PPOEnv([episode]); observation, _ = env.reset(seed=0)
        done = False
        while not done:
            masks = env.action_masks()
            probabilities = action_probabilities(model, observation, masks)
            if env.current == 0 and masks[INTENTS.index("PROBE_SHORT")]:
                rows.append({
                    "wait_probability": float(probabilities[INTENTS.index("WAIT")]),
                    "probe_probability": float(probabilities[INTENTS.index("PROBE_SHORT")]),
                })
            action = int(np.argmax(probabilities))
            observation, _, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
    return summarize_flat_probabilities(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("audit output already exists")
    model = MaskablePPO.load(args.policy, device="cpu")
    train = prepare_episodes(args.dataset, args.encoder, partition=Partition.TRAIN,
        purpose=DataPurpose.STAGE2_TRAIN)
    development = prepare_episodes(args.dataset, args.encoder,
        partition=Partition.DEVELOPMENT, purpose=DataPurpose.DEVELOPMENT_EVAL)
    report = {
        "protocol": PROTOCOL,
        "policy": str(args.policy),
        "training": audit_partition(model, train),
        "development": audit_partition(model, development),
        "policy_updated_during_audit": False,
        "sealed_test_accessed": False,
        "mode": "PAPER",
        "automatic_promotion": False,
        "position_permission": False,
        "order_permission": False,
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

