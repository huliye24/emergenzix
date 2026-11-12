#!/usr/bin/env python3
"""Build and audit the BRPM-feasible probe curriculum without training."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from emergenzix_api.brla_g2_ppo_env import prepare_episodes  # noqa: E402
from emergenzix_api.brla_g2_probe_curriculum import (  # noqa: E402
    PROTOCOL, audit_curriculum, build_curriculum,
)
from emergenzix_api.brla_g2_training_protocol import DataPurpose  # noqa: E402
from emergenzix_api.brmg_scenarios import Partition  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("curriculum audit output already exists")
    train = prepare_episodes(args.dataset, args.encoder, partition=Partition.TRAIN,
        purpose=DataPurpose.STAGE2_TRAIN)
    development = prepare_episodes(args.dataset, args.encoder,
        partition=Partition.DEVELOPMENT, purpose=DataPurpose.DEVELOPMENT_EVAL)
    report = {
        "protocol": PROTOCOL,
        "training": audit_curriculum(build_curriculum(train)),
        "development_audit_only": audit_curriculum(build_curriculum(development)),
        "future_outcome_use": "LABEL_ONLY_NOT_OBSERVATION",
        "development_used_for_fitting": False,
        "sealed_test_accessed": False,
        "mode": "PAPER",
        "automatic_promotion": False,
        "position_permission": False,
        "order_permission": False,
    }
    training = report["training"]
    checks = {
        "wait_and_probe_present": all(training["intent_counts"].get(name, 0) > 0
                                      for name in ("WAIT", "PROBE_SHORT")),
        "probe_coverage_at_least_0_50": training["probe_episode_coverage"] >= .50,
        "probe_fraction_bounded": .01 <= training["probe_fraction"] <= .35,
        "action_diversity_at_least_3": training["action_diversity"] >= 3,
        "all_labels_mask_valid": training["all_labels_mask_valid"],
        "future_fields_excluded_from_observation": not training["future_fields_in_observation"],
    }
    report["checks"] = checks
    report["decision"] = "READY_FOR_BEHAVIORAL_WARM_START" \
        if all(checks.values()) else "CURRICULUM_NOT_READY"
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
