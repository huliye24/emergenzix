#!/usr/bin/env python3
"""Run the frozen PPO policy once on unseen-seed PAPER exam paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from sb3_contrib import MaskablePPO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_ppo_env import BRLAG2PPOEnv, prepare_episodes  # noqa: E402
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    training_dataset_sha256 = torch.load(args.encoder, map_location="cpu",
                                         weights_only=False)["dataset_sha256"]
    episodes = prepare_episodes(args.dataset, args.encoder,
        partition=Partition.DEVELOPMENT, purpose=DataPurpose.DEVELOPMENT_EVAL,
        independent_exam=True)
    model = MaskablePPO.load(args.policy, device="cpu")
    results, actions = [], Counter()
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
        episode_actions = Counter(row["intent"] for row in env.trajectory)
        actions.update(episode_actions)
        results.append({"path_sha256": episode["path_sha256"],
            "return": env.equity - 1, "maximum_drawdown": max_drawdown,
            "intent_counts": dict(episode_actions),
            "probe_count": episode_actions["PROBE_SHORT"],
            "exposed_steps": sum(row["exposed"] for row in env.trajectory),
            "steps": len(env.trajectory),
            "brpm_rejections": sum(row["brpm_decision"] == "REJECT"
                                   for row in env.trajectory),
            "all_brgc_guarded": all(row["brgc_decision"] == "ALLOW_PAPER_TARGET"
                                    and row["order_permission"] is False
                                    for row in env.trajectory)})
    returns = np.asarray([row["return"] for row in results])
    drawdowns = np.asarray([row["maximum_drawdown"] for row in results])
    total_steps = sum(row["steps"] for row in results)
    exposure = sum(row["exposed_steps"] for row in results) / total_steps
    exposure_episode_coverage = sum(row["exposed_steps"] > 0
                                    for row in results) / len(results)
    rejection_rate = sum(row["brpm_rejections"] for row in results) / total_steps
    probe_coverage = sum(row["probe_count"] > 0 for row in results) / len(results)
    checks = {"dataset_hash_differs_from_training": manifest["dataset_sha256"]
              != training_dataset_sha256,
        "seeds_do_not_overlap_training": manifest["seed_overlap_with_training"] is False,
        "positive_median_return": float(np.median(returns)) > 0,
        "positive_path_fraction_at_least_0_60": float((returns > 0).mean()) >= .60,
        "minimum_return_at_least_minus_0_10": float(returns.min()) >= -.10,
        "maximum_drawdown_at_most_0_10": float(drawdowns.max()) <= .10,
        "probe_coverage_at_least_0_50": probe_coverage >= .50,
        "action_diversity_at_least_3": sum(value > 0 for value in actions.values()) >= 3,
        "effective_exposure_fraction": .05 <= exposure <= .80,
        "effective_exposure_episode_coverage": exposure_episode_coverage >= .80,
        "brpm_rejection_rate_at_most_0_05": rejection_rate <= .05,
        "all_brgc_guarded": all(row["all_brgc_guarded"] for row in results)}
    report = {"protocol": "emergenzix-brla-g2-independent-paper-exam-v1",
        "exam_dataset_sha256": manifest["dataset_sha256"],
        "training_dataset_sha256": training_dataset_sha256,
        "exam_episode_count": len(results), "policy_training_steps": 0,
        "policy_updated_during_exam": False,
        "mean_return": float(returns.mean()), "median_return": float(np.median(returns)),
        "minimum_return": float(returns.min()), "positive_path_fraction": float((returns > 0).mean()),
        "maximum_drawdown": float(drawdowns.max()), "probe_coverage": probe_coverage,
        "exposure_fraction": exposure, "intent_counts": dict(actions),
        "exposure_episode_coverage": exposure_episode_coverage,
        "brpm_rejection_rate": rejection_rate,
        "brpm_rejections": sum(row["brpm_rejections"] for row in results),
        "checks": checks, "decision": "PASS_INDEPENDENT_SYNTHETIC_EXAM"
            if all(checks.values()) else "FAIL_INDEPENDENT_SYNTHETIC_EXAM",
        "episodes": results, "sealed_test_accessed": False,
        "mode": "PAPER", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key != "episodes"}, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
