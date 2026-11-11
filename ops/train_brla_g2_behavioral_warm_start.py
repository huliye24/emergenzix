#!/usr/bin/env python3
"""Train a masked behavioral warm start using TRAIN curriculum labels only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv
from sb3_contrib import MaskablePPO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_behavioral_warm_start import (  # noqa: E402
    PROTOCOL, calibrated_class_weights, classification_audit,
    warm_start_qualification_checks,
)
from emergenzix_api.brla_g2_ppo_env import (  # noqa: E402
    BRLAG2PPOEnv, INTENTS, OBSERVATION_NAMES, prepare_episodes,
)
from emergenzix_api.brla_g2_probe_curriculum import (  # noqa: E402
    PROTOCOL as CURRICULUM_PROTOCOL, build_curriculum,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def parameter_sha256(model: MaskablePPO) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.policy.state_dict().items()):
        array = value.detach().cpu().contiguous().numpy()
        digest.update(name.encode()); digest.update(array.tobytes())
    return digest.hexdigest()


def tensors(examples, device):
    observations = torch.as_tensor(np.stack([row.observation for row in examples]),
                                   dtype=torch.float32, device=device)
    masks = np.stack([row.action_mask for row in examples]).astype(bool)
    actions = torch.as_tensor([row.action for row in examples], dtype=torch.long,
                              device=device)
    return observations, masks, actions


def evaluate(model: MaskablePPO, examples) -> dict[str, object]:
    observations, masks, actions = tensors(examples, model.device)
    with torch.no_grad():
        distribution = model.policy.get_distribution(observations, action_masks=masks)
        predictions = distribution.distribution.probs.argmax(dim=1).cpu().numpy()
    return classification_audit(actions.cpu().numpy().tolist(), predictions.tolist(), masks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=20261111)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("output directory already exists")
    train_episodes = prepare_episodes(args.dataset, args.encoder,
        partition=Partition.TRAIN, purpose=DataPurpose.STAGE2_TRAIN)
    development_episodes = prepare_episodes(args.dataset, args.encoder,
        partition=Partition.DEVELOPMENT, purpose=DataPurpose.DEVELOPMENT_EVAL)
    train = build_curriculum(train_episodes)
    development = build_curriculum(development_episodes)
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(args.seed)
    env = DummyVecEnv([lambda: Monitor(BRLAG2PPOEnv(train_episodes))])
    model = MaskablePPO("MlpPolicy", env, seed=args.seed,
        device="cuda" if torch.cuda.is_available() else "cpu",
        learning_rate=3e-4, n_steps=512, batch_size=256, n_epochs=6,
        gamma=.995, gae_lambda=.95, clip_range=.2, ent_coef=.01,
        policy_kwargs={"net_arch": [128, 128]}, verbose=0)
    observations, masks, actions = tensors(train, model.device)
    counts = Counter(actions.cpu().numpy().tolist())
    class_weights = torch.ones(len(INTENTS), dtype=torch.float32, device=model.device)
    frozen_weights = calibrated_class_weights(counts)
    for action, weight in frozen_weights.items():
        class_weights[action] = weight
    generator = torch.Generator(device="cpu"); generator.manual_seed(args.seed)
    losses = []
    for _ in range(args.epochs):
        permutation = torch.randperm(len(train), generator=generator)
        for start in range(0, len(train), args.batch_size):
            indices = permutation[start:start + args.batch_size].to(model.device)
            batch_masks = masks[indices.cpu().numpy()]
            distribution = model.policy.get_distribution(
                observations[indices], action_masks=batch_masks)
            log_prob = distribution.log_prob(actions[indices])
            loss = -(class_weights[actions[indices]] * log_prob).mean()
            model.policy.optimizer.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.policy.parameters(), .5)
            model.policy.optimizer.step(); losses.append(float(loss.detach().cpu()))
    training_audit = evaluate(model, train)
    development_audit = evaluate(model, development)
    checks = warm_start_qualification_checks(training_audit, development_audit)
    args.output.mkdir(parents=True)
    model.save(args.output / "model")
    report = {
        "protocol": PROTOCOL,
        "curriculum_protocol": CURRICULUM_PROTOCOL,
        "algorithm": "MASKED_CLASS_BALANCED_BEHAVIOR_CLONING",
        "seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size,
        "train_example_count": len(train),
        "development_example_count": len(development),
        "development_used_for_fitting": False,
        "class_weight_protocol": "SQRT_INVERSE_FREQUENCY_WITH_EXIT_1_5X",
        "class_weights": {INTENTS[action]: weight
                          for action, weight in frozen_weights.items()},
        "final_training_loss": losses[-1],
        "training": training_audit, "development": development_audit,
        "checks": checks,
        "decision": "READY_FOR_PPO_FINE_TUNING" if all(checks.values())
                    else "WARM_START_NOT_READY",
        "observation_names": OBSERVATION_NAMES,
        "policy_parameters_sha256": parameter_sha256(model),
        "sealed_test_accessed": False, "mode": "PAPER",
        "automatic_promotion": False, "position_permission": False,
        "order_permission": False,
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    temporary = args.output / "report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output / "report.json")
    print(json.dumps(report, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
