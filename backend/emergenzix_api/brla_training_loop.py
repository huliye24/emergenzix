"""Auditable PAPER loop connecting alpha, risk, cost, execution and memory."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


PROTOCOL = "emergenzix-brla-training-loop-smoke-v1"
ALLOWED_TARGETS = (0.0, -0.10, -0.25, -0.50)


def observation(bars: Sequence[Mapping[str, Any]], index: int) -> dict[str, float]:
    if index < 12 or index >= len(bars):
        raise ValueError("observation requires 12 completed bars")
    closes = [float(row["close"]) for row in bars[index - 12:index + 1]]
    volumes = [float(row["base_volume"]) for row in bars[index - 11:index + 1]]
    if any(not math.isfinite(value) or value <= 0 for value in closes + volumes):
        raise ValueError("observation values must be finite and positive")
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    volatility = math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
    return {
        "return_1": closes[-1] / closes[-2] - 1,
        "return_3": closes[-1] / closes[-4] - 1,
        "return_12": closes[-1] / closes[0] - 1,
        "drawdown_from_12_high": closes[-1] / max(closes) - 1,
        "volume_ratio": volumes[-1] / (sum(volumes[:-1]) / len(volumes[:-1])),
        "volatility_12": volatility,
    }


def theory_prior_alpha(obs: Mapping[str, float]) -> float:
    """Temporary demonstrator only; this is not the trainable BRLA policy."""
    if obs["return_12"] >= 0 or obs["return_3"] >= 0:
        return 0.0
    pressure = (-obs["return_3"] / max(obs["volatility_12"], 1e-9)
                + max(obs["volume_ratio"] - .8, 0))
    if pressure >= 4:
        return -0.50
    if pressure >= 2.5:
        return -0.25
    if pressure >= 1.5:
        return -0.10
    return 0.0


def risk_approve(target: float, *, drawdown: float) -> tuple[float, str]:
    if not math.isfinite(target) or target > 0 or target < -.50 or target not in ALLOWED_TARGETS:
        return 0.0, "INVALID_TARGET_FLAT"
    if drawdown >= .20:
        return 0.0, "DRAWDOWN_KILL_SWITCH"
    if drawdown >= .10 and target < -.10:
        return -.10, "DRAWDOWN_CAP"
    return target, "APPROVED"


@dataclass(frozen=True)
class CostModel:
    fee_and_slippage_per_fraction_change: float = .0015

    def cost(self, previous: float, target: float) -> float:
        return abs(target - previous) * self.fee_and_slippage_per_fraction_change


def run_episode(path: Mapping[str, Any], *, episode_id: str,
                cost_model: CostModel = CostModel(),
                policy: Callable[[Mapping[str, float]], float] = theory_prior_alpha,
                policy_name: str = "THEORY_PRIOR_DEMONSTRATOR") -> dict[str, Any]:
    bars = path["bars"]
    if len(bars) < 15 or path.get("synthetic") is not True:
        raise ValueError("a labeled synthetic path with sufficient bars is required")
    equity = peak = 1.0
    current = 0.0
    turnover = costs = max_drawdown = 0.0
    experiences = []
    for index in range(12, len(bars) - 1):
        obs = observation(bars, index)
        proposed = float(policy(obs))
        drawdown_before = (peak - equity) / peak
        target, risk_reason = risk_approve(proposed, drawdown=drawdown_before)
        execution_bar = bars[index + 1]
        market_return = float(execution_bar["close"]) / float(execution_bar["open"]) - 1
        transaction_cost = cost_model.cost(current, target)
        step_return = target * market_return - transaction_cost
        equity_before = equity
        equity *= max(.05, 1 + step_return)
        peak = max(peak, equity)
        drawdown_after = (peak - equity) / peak
        max_drawdown = max(max_drawdown, drawdown_after)
        reward = step_return - max(drawdown_after - drawdown_before, 0) * .25
        turnover += abs(target - current)
        costs += transaction_cost
        experiences.append({
            "episode_id": episode_id, "decision_index": index,
            "decision_timestamp": bars[index]["timestamp"],
            "observation": obs, "proposed_target": proposed,
            "approved_target": target, "risk_reason": risk_reason,
            "execution_timestamp": execution_bar["timestamp"],
            "execution_open": float(execution_bar["open"]),
            "market_return": market_return, "transaction_cost": transaction_cost,
            "equity_before": equity_before, "equity_after": equity,
            "reward": reward, "next_observation": observation(bars, index + 1),
            "done": index == len(bars) - 2,
        })
        current = target
    result = {"protocol": PROTOCOL, "episode_id": episode_id,
              "policy_name": policy_name,
              "path_sha256": path["path_sha256"], "final_return": equity - 1,
              "max_drawdown": max_drawdown,
              "turnover": turnover, "transaction_cost": costs,
              "experience_count": len(experiences), "experiences": experiences,
              "mode": "PAPER", "automatic_promotion": False,
              "order_permission": False}
    fingerprinted = {key: value for key, value in result.items() if key != "experiences"}
    result["episode_sha256"] = hashlib.sha256(json.dumps(
        fingerprinted, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
