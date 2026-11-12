#!/usr/bin/env python3
"""Train stage-two PPO against frozen encoder outputs and BRPM/BRGC authority."""

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
from emergenzix_api.brla_g2_ppo_env import (  # noqa: E402
    BRLAG2PPOEnv, INTENTS, OBSERVATION_NAMES, PROTOCOL, prepare_episodes,
)
from emergenzix_api.brla_g2_ppo_fine_tuning import (  # noqa: E402
    PROTOCOL as FINE_TUNING_PROTOCOL, authorize_warm_start,
    fine_tuning_chunks, fine_tuning_evaluation_scope,
)
from emergenzix_api.brla_g2_behavioral_warm_start import calibrated_class_weights  # noqa: E402
from emergenzix_api.brla_g2_probe_curriculum import build_curriculum  # noqa: E402
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def parameter_sha256(model: MaskablePPO) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.policy.state_dict().items()):
        array = value.detach().cpu().contiguous().numpy()
        digest.update(name.encode()); digest.update(str(array.dtype).encode())
        digest.update(json.dumps(array.shape).encode()); digest.update(array.tobytes())
    return digest.hexdigest()


def evaluate(model: MaskablePPO, episodes) -> dict[str, object]:
    rows = []
    for episode in episodes:
        env = BRLAG2PPOEnv([episode]); observation, _ = env.reset(seed=0)
        peak = 1.0; max_drawdown = 0.0; done = False
        while not done:
            action, _ = model.predict(observation, deterministic=True,
                                      action_masks=env.action_masks())
            observation, _, terminated, truncated, info = env.step(int(action))
            peak = max(peak, info["equity"])
            max_drawdown = max(max_drawdown, (peak - info["equity"]) / peak)
            done = terminated or truncated
        rows.append({"path_sha256": episode["path_sha256"],
            "return": env.equity - 1, "max_drawdown": max_drawdown,
            "actions": dict(Counter(row["intent"] for row in env.trajectory)),
            "brpm_rejections": sum(row["brpm_decision"] == "REJECT" for row in env.trajectory),
            "probe_count": sum(row["intent"] == "PROBE_SHORT" for row in env.trajectory),
            "exposed_steps": sum(row["exposed"] for row in env.trajectory),
            "steps": len(env.trajectory),
            "missed_opportunity_penalty": float(sum(
                row["missed_opportunity"] for row in env.trajectory)),
            "all_brgc_guarded": all(row["brgc_decision"] == "ALLOW_PAPER_TARGET"
                                    and row["order_permission"] is False
                                    for row in env.trajectory)})
    returns = np.asarray([row["return"] for row in rows])
    drawdowns = np.asarray([row["max_drawdown"] for row in rows])
    actions = Counter(); [actions.update(row["actions"]) for row in rows]
    total_steps = sum(row["steps"] for row in rows)
    probe_coverage = sum(row["probe_count"] > 0 for row in rows) / len(rows)
    exposure_fraction = sum(row["exposed_steps"] for row in rows) / total_steps
    exposure_episode_coverage = sum(row["exposed_steps"] > 0 for row in rows) / len(rows)
    rejection_rate = sum(row["brpm_rejections"] for row in rows) / total_steps
    checks = {"nonzero_probe_coverage": probe_coverage >= .50,
        "action_diversity_at_least_3": sum(count > 0 for count in actions.values()) >= 3,
        "effective_exposure_fraction": .05 <= exposure_fraction <= .80,
        "effective_exposure_episode_coverage": exposure_episode_coverage >= .80,
        "brpm_rejection_rate_at_most_0_05": rejection_rate <= .05,
        "positive_mean_return": float(returns.mean()) > 0,
        "maximum_drawdown_at_most_0_10": float(drawdowns.max()) <= .10,
        "all_brgc_guarded": all(row["all_brgc_guarded"] for row in rows)}
    return {"episode_count": len(rows), "mean_return": float(returns.mean()),
        "median_return": float(np.median(returns)), "minimum_return": float(returns.min()),
        "maximum_drawdown": float(drawdowns.max()),
        "intent_counts": dict(actions), "probe_episode_coverage": probe_coverage,
        "exposure_fraction": exposure_fraction,
        "exposure_episode_coverage": exposure_episode_coverage,
        "brpm_rejection_rate": rejection_rate, "qualification_checks": checks,
        "decision": "READY_FOR_FURTHER_PAPER_VALIDATION" if all(checks.values())
                    else "NOT_READY",
        "all_brgc_guarded": all(row["all_brgc_guarded"] for row in rows),
        "sealed_test_accessed": False, "episodes": rows}


def behavior_anchor(model: MaskablePPO, examples, *, epochs: int, seed: int) -> float:
    observations = torch.as_tensor(np.stack([row.observation for row in examples]),
        dtype=torch.float32, device=model.device)
    masks = np.stack([row.action_mask for row in examples]).astype(bool)
    actions = torch.as_tensor([row.action for row in examples], dtype=torch.long,
                              device=model.device)
    counts = Counter(actions.cpu().numpy().tolist())
    weights = calibrated_class_weights(counts)
    class_weights = torch.ones(len(INTENTS), dtype=torch.float32, device=model.device)
    for action, weight in weights.items(): class_weights[action] = weight
    generator = torch.Generator(device="cpu"); generator.manual_seed(seed)
    final_loss = 0.0
    for _ in range(epochs):
        permutation = torch.randperm(len(examples), generator=generator)
        for start in range(0, len(examples), 256):
            indices = permutation[start:start + 256].to(model.device)
            distribution = model.policy.get_distribution(observations[indices],
                action_masks=masks[indices.cpu().numpy()])
            loss = -(class_weights[actions[indices]]
                     * distribution.log_prob(actions[indices])).mean()
            model.policy.optimizer.zero_grad(); loss.backward()
            torch.nn.utils.clip_grad_norm_(model.policy.parameters(), .5)
            model.policy.optimizer.step(); final_loss = float(loss.detach().cpu())
    return final_loss


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timesteps", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--initial-policy", type=Path)
    parser.add_argument("--initial-policy-report", type=Path)
    parser.add_argument("--behavior-anchor-interval", type=int)
    parser.add_argument("--behavior-anchor-epochs", type=int, default=1)
    parser.add_argument("--train-only-stability-audit", action="store_true")
    args = parser.parse_args()
    if args.output.exists(): raise FileExistsError("output directory already exists")
    if bool(args.initial_policy) != bool(args.initial_policy_report):
        raise ValueError("initial policy and qualification report must be supplied together")
    if args.behavior_anchor_interval and not args.initial_policy:
        raise ValueError("behavior anchoring requires a qualified initial policy")
    train = prepare_episodes(args.dataset, args.encoder, partition=Partition.TRAIN,
        purpose=DataPurpose.STAGE2_TRAIN)
    development = [] if args.train_only_stability_audit else prepare_episodes(
        args.dataset, args.encoder, partition=Partition.DEVELOPMENT,
        purpose=DataPurpose.DEVELOPMENT_EVAL)
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(args.seed)
    env = DummyVecEnv([lambda: Monitor(BRLAG2PPOEnv(train))])
    initial_model_file_sha256 = None
    initial_policy_parameters_sha256 = None
    if args.initial_policy:
        initial_model_file_sha256 = hashlib.sha256(args.initial_policy.read_bytes()).hexdigest()
        qualification = json.loads(args.initial_policy_report.read_text(encoding="utf-8"))
        authorize_warm_start(qualification, initial_model_file_sha256)
        model = MaskablePPO.load(args.initial_policy, env=env,
            device="cuda" if torch.cuda.is_available() else "cpu")
        initial_policy_parameters_sha256 = parameter_sha256(model)
    else:
        model = MaskablePPO("MlpPolicy", env, seed=args.seed,
            device="cuda" if torch.cuda.is_available() else "cpu",
            learning_rate=3e-4, n_steps=512, batch_size=256, n_epochs=6,
            gamma=.995, gae_lambda=.95, clip_range=.2, ent_coef=.01,
            policy_kwargs={"net_arch": [128, 128]}, verbose=0)
    anchor_updates = 0; final_anchor_loss = None
    if args.behavior_anchor_interval:
        curriculum = build_curriculum(train)
        for index, chunk in enumerate(fine_tuning_chunks(
                args.timesteps, args.behavior_anchor_interval)):
            model.learn(total_timesteps=chunk, progress_bar=False,
                        reset_num_timesteps=False)
            final_anchor_loss = behavior_anchor(model, curriculum,
                epochs=args.behavior_anchor_epochs, seed=args.seed + index)
            anchor_updates += 1
    else:
        model.learn(total_timesteps=args.timesteps, progress_bar=False,
                    reset_num_timesteps=not bool(args.initial_policy))
    evaluation_partition, evaluation_key = fine_tuning_evaluation_scope(
        args.train_only_stability_audit)
    evaluation_report = evaluate(model, train if args.train_only_stability_audit
                                 else development)
    args.output.mkdir(parents=True)
    model.save(args.output / "model")
    report = {"protocol": PROTOCOL, "training_protocol": FINE_TUNING_PROTOCOL
              if args.initial_policy else "SCRATCH_PPO",
        "algorithm": "sb3-contrib/MaskablePPO",
        "sb3_version": __import__("stable_baselines3").__version__,
        "encoder_model_sha256": torch.load(args.encoder, map_location="cpu",
            weights_only=False)["model_sha256"],
        "dataset_sha256": json.loads((args.dataset / "manifest.json").read_text())["dataset_sha256"],
        "seed": args.seed, "timesteps": args.timesteps,
        "initial_model_file_sha256": initial_model_file_sha256,
        "initial_policy_parameters_sha256": initial_policy_parameters_sha256,
        "behavior_anchor_interval": args.behavior_anchor_interval,
        "behavior_anchor_epochs": args.behavior_anchor_epochs
            if args.behavior_anchor_interval else 0,
        "behavior_anchor_updates": anchor_updates,
        "final_behavior_anchor_loss": final_anchor_loss,
        "train_episode_count": len(train), "development_episode_count": len(development),
        "evaluation_partition": evaluation_partition,
        "observation_names": OBSERVATION_NAMES, "intents": INTENTS,
        "policy_parameters_sha256": parameter_sha256(model),
        evaluation_key: evaluation_report, "sealed_test_accessed": False,
        "mode": "PAPER", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    temporary = args.output / "report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output / "report.json")
    print(json.dumps({key: value for key, value in report.items()
                      if key != "development"}, indent=2))
    print(json.dumps({key: value for key, value in evaluation_report.items()
                      if key != "episodes"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
