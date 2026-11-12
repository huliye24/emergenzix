#!/usr/bin/env python3
"""Train BRLA-G1 with the pinned Stable-Baselines3 PPO implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_sb3_env import ACTIONS, OBSERVATION_NAMES, BRLASB3Env  # noqa: E402
from emergenzix_api.brmg_scenarios import (  # noqa: E402
    Partition, partition_manifest_from_dict, scenario_as_training_path,
    scenarios_from_bundle, select_partition,
)


def parameter_sha256(model: PPO) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.policy.state_dict().items()):
        digest.update(name.encode()); digest.update(value.detach().cpu().numpy().tobytes())
    return digest.hexdigest()


def evaluate(model: PPO, paths, seed: int) -> dict[str, object]:
    env = BRLASB3Env(paths)
    observation, _ = env.reset(seed=seed)
    done = False
    while not done:
        action, _ = model.predict(observation, deterministic=True)
        observation, _, terminated, truncated, _ = env.step(int(action))
        done = terminated or truncated
    return {"trajectory_sha256": env.trajectory_sha256(), "final_return": env.equity - 1,
            "steps": len(env.trajectory), "all_guarded": all(
                row["guard_decision"] == "ALLOW_PAPER_TARGET" for row in env.trajectory)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-bundle", type=Path, required=True)
    parser.add_argument("--partition-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=20261111)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("output directory already exists")
    scenarios = scenarios_from_bundle(json.loads(
        args.scenario_bundle.read_text(encoding="utf-8")))
    manifest = partition_manifest_from_dict(json.loads(
        args.partition_manifest.read_text(encoding="utf-8")))
    train_scenarios = select_partition(manifest, scenarios, purpose=Partition.TRAIN)
    development_scenarios = select_partition(
        manifest, scenarios, purpose=Partition.DEVELOPMENT)
    train_paths = [scenario_as_training_path(row) for row in train_scenarios]
    development_paths = [scenario_as_training_path(row) for row in development_scenarios]
    if not train_paths or not development_paths:
        raise ValueError("non-empty TRAIN and DEVELOPMENT partitions are required")
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(args.seed)
    def make_env():
        return Monitor(BRLASB3Env(train_paths))
    env = DummyVecEnv([make_env])
    model = PPO("MlpPolicy", env, seed=args.seed, device="auto", learning_rate=3e-4,
                n_steps=256, batch_size=256, n_epochs=4, gamma=.995, gae_lambda=.95,
                clip_range=.2, ent_coef=.01, policy_kwargs={"net_arch": [64, 64]}, verbose=0)
    model.learn(total_timesteps=args.timesteps, progress_bar=False)
    evaluation = evaluate(model, development_paths, args.seed)
    args.output.mkdir(parents=True)
    model.save(args.output / "model")
    report = {"schema_version": "emergenzix-brla-g1-finrl-sb3-ppo-v1",
        "algorithm_implementation": "stable-baselines3/PPO", "sb3_version": __import__(
            "stable_baselines3").__version__, "seed": args.seed, "timesteps": args.timesteps,
        "partition_manifest_sha256": manifest.manifest_sha256,
        "train_episode_count": len(train_paths),
        "development_episode_count": len(development_paths),
        "sealed_test_accessed": False,
        "observations": OBSERVATION_NAMES, "actions": ACTIONS, "cost_rate": .0015,
        "risk_constitution": "BRLA reflexive risk constitution v1",
        "policy_parameters_sha256": parameter_sha256(model), "development": evaluation,
        "mode": "PAPER", "automatic_promotion": False, "order_permission": False}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    temporary = args.output / "report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output / "report.json")


if __name__ == "__main__":
    main()
