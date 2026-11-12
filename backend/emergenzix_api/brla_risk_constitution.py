"""Frozen BRLA reflexive-risk constitution for PAPER research.

The policy may learn which short target to propose.  It may not learn whether
these safety constraints apply.  Stop-based risk release is deliberately not
implemented until the environment carries an auditable stop price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ReflexiveRiskConstitution:
    maximum_short_target: float = -0.50
    initial_short_target: float = -0.10
    maximum_build_step: float = 0.10
    drawdown_cap: float = 0.10
    drawdown_kill_switch: float = 0.20
    drawdown_penalty: float = 1.00
    exposure_penalty: float = 0.0010
    build_penalty: float = 0.0005
    loss_add_penalty: float = 0.0200


CONSTITUTION = ReflexiveRiskConstitution()


def approve_target(
    proposed: float,
    *,
    current: float,
    drawdown: float,
    previous_step_return: float,
    rules: ReflexiveRiskConstitution = CONSTITUTION,
) -> tuple[float, str]:
    """Fail closed and enforce small, gradual, non-loss-adding short exposure."""
    values = (proposed, current, drawdown, previous_step_return)
    if any(not math.isfinite(value) for value in values):
        return 0.0, "INVALID_STATE_FLAT"
    if proposed > 0 or proposed < rules.maximum_short_target:
        return 0.0, "INVALID_TARGET_FLAT"
    if drawdown >= rules.drawdown_kill_switch:
        return 0.0, "DRAWDOWN_KILL_SWITCH"
    if drawdown >= rules.drawdown_cap and proposed < rules.initial_short_target:
        return rules.initial_short_target, "DRAWDOWN_CAP"
    if current == 0 and proposed < rules.initial_short_target:
        return rules.initial_short_target, "INITIAL_PROBE_CAP"
    increasing = proposed < current
    if increasing and previous_step_return < 0:
        return current, "LOSS_ADD_BLOCKED"
    gradual_floor = max(rules.maximum_short_target, current - rules.maximum_build_step)
    if proposed < gradual_floor:
        return gradual_floor, "GRADUAL_BUILD_CAP"
    return proposed, "APPROVED"


def constitutional_reward(
    *,
    step_return: float,
    drawdown_increase: float,
    current: float,
    approved: float,
    loss_add_attempted: bool,
    rules: ReflexiveRiskConstitution = CONSTITUTION,
) -> tuple[float, dict[str, float]]:
    """Return the frozen reward and an auditable component breakdown."""
    components = {
        "net_return": step_return,
        "drawdown": -max(drawdown_increase, 0.0) * rules.drawdown_penalty,
        "exposure": -abs(approved) * rules.exposure_penalty,
        "build": -max(abs(approved) - abs(current), 0.0) * rules.build_penalty,
        "loss_add": -rules.loss_add_penalty if loss_add_attempted else 0.0,
    }
    return sum(components.values()), components
