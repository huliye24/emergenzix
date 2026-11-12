"""Gymnasium BRLA environment for the pinned FinRL/SB3 research runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .brgc_guard import gate_paper_target
from .brla_risk_constitution import approve_target, constitutional_reward
from .brla_training_loop import CostModel, observation

FEATURES = ("return_1", "return_3", "return_12", "drawdown_from_12_high",
            "volume_ratio", "volatility_12")
ACTIONS = (0.0, -0.10, -0.25, -0.50)
OBSERVATION_NAMES = FEATURES + ("current_position", "current_drawdown", "previous_step_return")


class BRLASB3Env(gym.Env):
    """PAPER environment: an SB3 action is a proposal, never an approved target."""

    metadata = {"render_modes": []}

    def __init__(self, paths: Sequence[Mapping[str, Any]], *,
                 cost_model: CostModel = CostModel()) -> None:
        super().__init__()
        if not paths or any(path.get("synthetic") is not True or len(path.get("bars", [])) < 15
                            for path in paths):
            raise ValueError("non-empty synthetic paths with at least 15 bars are required")
        self.paths = list(paths)
        self.cost_model = cost_model
        self.action_space = spaces.Discrete(len(ACTIONS))
        self.observation_space = spaces.Box(-np.inf, np.inf,
                                            shape=(len(OBSERVATION_NAMES),), dtype=np.float32)
        self.trajectory: list[dict[str, Any]] = []

    def _state(self) -> np.ndarray:
        obs = observation(self.bars, self.index)
        return np.asarray([*(obs[name] for name in FEATURES), self.current,
                           (self.peak - self.equity) / self.peak,
                           self.previous_step_return], dtype=np.float32)

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        selected = int(self.np_random.integers(0, len(self.paths)))
        self.path = self.paths[selected]
        self.bars = self.path["bars"]
        self.index = 12
        self.equity = self.peak = 1.0
        self.current = self.previous_step_return = 0.0
        self.trajectory = []
        return self._state(), {"path_sha256": self.path["path_sha256"], "mode": "PAPER"}

    def step(self, action: int):
        proposed = ACTIONS[int(action)]
        drawdown_before = (self.peak - self.equity) / self.peak
        loss_add_attempted = proposed < self.current and self.previous_step_return < 0
        allocated, allocation_reason = approve_target(
            proposed, current=self.current, drawdown=drawdown_before,
            previous_step_return=self.previous_step_return)
        guard = gate_paper_target(allocated, hard_maximum_short_fraction=.50,
                                  market_data_fresh=True, account_state_certain=True,
                                  risk_state_valid=True)
        target = guard.approved_paper_target_fraction
        execution_bar = self.bars[self.index + 1]
        market_return = float(execution_bar["close"]) / float(execution_bar["open"]) - 1
        transaction_cost = self.cost_model.cost(self.current, target)
        step_return = target * market_return - transaction_cost
        next_equity = self.equity * max(.05, 1 + step_return)
        next_peak = max(self.peak, next_equity)
        drawdown_after = (next_peak - next_equity) / next_peak
        reward, reward_components = constitutional_reward(
            step_return=step_return, drawdown_increase=drawdown_after - drawdown_before,
            current=self.current, approved=target, loss_add_attempted=loss_add_attempted)
        self.trajectory.append({"index": self.index, "action": int(action),
            "proposed": proposed, "allocated": allocated, "approved": target,
            "allocation_reason": allocation_reason, "guard_decision": guard.decision,
            "guard_reason": guard.reason_code, "cost": transaction_cost,
            "reward": reward, "reward_components": reward_components})
        self.equity, self.peak = next_equity, next_peak
        self.previous_step_return, self.current = step_return, target
        self.index += 1
        terminated = self.index >= len(self.bars) - 1
        state = np.zeros(len(OBSERVATION_NAMES), dtype=np.float32) if terminated else self._state()
        info = {"mode": guard.mode, "order_permission": guard.order_permission,
                "automatic_promotion": guard.automatic_promotion,
                "proposed_target": proposed, "approved_target": target,
                "guard_decision": guard.decision, "guard_reason": guard.reason_code,
                "equity": self.equity}
        return state, float(reward), terminated, False, info

    def trajectory_sha256(self) -> str:
        payload = json.dumps(self.trajectory, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()
