"""BRPM-feasible behavioral curriculum for PPO policy warm starts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .brla_g2_ppo_env import BRLAG2PPOEnv, INTENTS

PROTOCOL = "emergenzix-brla-g2-probe-curriculum-v2"


@dataclass(frozen=True, slots=True)
class CurriculumExample:
    observation: np.ndarray
    action_mask: np.ndarray
    action: int
    path_sha256: str
    step_index: int
    future_fields_in_observation: bool = False
    position_permission: bool = False
    order_permission: bool = False


def teacher_action(env: BRLAG2PPOEnv) -> int:
    """Choose a conservative legal label; future return is label-only evidence."""
    masks = env.action_masks()
    row = env.rows[env.index]
    probabilities = row["encoder"][:6]
    old_strength, new_strength = map(float, row["encoder"][6:8])
    maturity, remaining, quality, exhaustion, late_risk = map(
        float, row["encoder"][8:13])
    if env.current == 0:
        probe = INTENTS.index("PROBE_SHORT")
        net_probe_return = .10 * max(-row["market_return"], 0.0) - .10 * row["cost_rate"]
        eligible = (masks[probe] and .15 <= maturity < .70 and remaining >= .35
                    and quality >= .25 and late_risk < .70 and net_probe_return > 0)
        return probe if eligible else INTENTS.index("WAIT")
    exit_action = INTENTS.index("EXIT")
    structure_restored = (float(probabilities[4] + probabilities[5]) >= .65
                          and new_strength >= old_strength + .10)
    if masks[exit_action] and (exhaustion >= .70 or late_risk >= .80
                               or structure_restored):
        return exit_action
    return INTENTS.index("HOLD")


def build_curriculum(episodes: Sequence[dict[str, Any]]) -> tuple[CurriculumExample, ...]:
    examples = []
    for episode in episodes:
        env = BRLAG2PPOEnv([episode]); observation, _ = env.reset(seed=0)
        done = False
        while not done:
            masks = env.action_masks(); action = teacher_action(env)
            if not bool(masks[action]):
                raise PermissionError("curriculum teacher selected a masked action")
            examples.append(CurriculumExample(observation.copy(), masks.copy(), action,
                episode["path_sha256"], env.index))
            observation, _, terminated, truncated, _ = env.step(action)
            done = terminated or truncated
    if not examples:
        raise ValueError("curriculum requires prepared episodes")
    return tuple(examples)


def audit_curriculum(examples: Sequence[CurriculumExample]) -> dict[str, object]:
    if not examples:
        raise ValueError("curriculum audit requires examples")
    actions = Counter(INTENTS[example.action] for example in examples)
    paths = {example.path_sha256 for example in examples}
    probe_paths = {example.path_sha256 for example in examples
                   if INTENTS[example.action] == "PROBE_SHORT"}
    all_legal = all(bool(example.action_mask[example.action]) for example in examples)
    return {
        "example_count": len(examples),
        "path_count": len(paths),
        "intent_counts": dict(actions),
        "probe_fraction": actions["PROBE_SHORT"] / len(examples),
        "probe_episode_coverage": len(probe_paths) / len(paths),
        "action_diversity": sum(count > 0 for count in actions.values()),
        "all_labels_mask_valid": all_legal,
        "future_fields_in_observation": any(
            example.future_fields_in_observation for example in examples),
        "position_permission": False,
        "order_permission": False,
    }
