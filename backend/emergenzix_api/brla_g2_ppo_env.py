"""Constrained stage-two PPO environment using the frozen BRLA-G2 encoder."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Sequence

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .brgc_guard import gate_paper_target
from .brla_g2_order_encoder import WINDOWS, build_examples, create_encoder
from .brla_g2_training_protocol import DataPurpose, OrderState, authorize_data_use
from .brla_g2_trend_timing import stage2_timing_reward
from .brmg_scenarios import Partition
from .brpm_g1 import BRPMRequest, RiskLedgerEntry, allocate_short_risk, verify_risk_ledger

PROTOCOL = "emergenzix-brla-g2-constrained-ppo-env-v5"
INTENTS = ("WAIT", "PROBE_SHORT", "HOLD", "INCREASE_SHORT", "REDUCE_SHORT", "EXIT")
OBSERVATION_NAMES = tuple(f"state_probability_{state.value}" for state in OrderState) + (
    "old_order_strength", "new_order_strength", "trend_maturity", "remaining_potential",
    "entry_location_quality", "feedback_exhaustion", "late_entry_risk",
    "current_position", "current_drawdown", "previous_step_return",
    "remaining_risk_budget")


def prepare_episodes(directory: Path, checkpoint_path: Path, *, partition: Partition,
                     purpose: DataPurpose, stride_bars: int = 96,
                     independent_exam: bool = False) -> list[dict[str, Any]]:
    import torch

    authorize_data_use(partition, purpose)
    if purpose not in {DataPurpose.STAGE2_TRAIN, DataPurpose.DEVELOPMENT_EVAL}:
        raise PermissionError("PPO preparation may only use TRAIN and DEVELOPMENT")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    is_exam = manifest.get("protocol") == "emergenzix-brmg-g2-independent-exam-v1"
    if independent_exam and not (is_exam and purpose is DataPurpose.DEVELOPMENT_EVAL
                                 and manifest.get("training_allowed") is False):
        raise PermissionError("independent exam requires its frozen PAPER manifest")
    if not independent_exam and manifest["dataset_sha256"] != checkpoint["dataset_sha256"]:
        raise ValueError("frozen encoder and scenario dataset identities disagree")
    model = create_encoder(torch); model.load_state_dict(checkpoint["state_dict"]); model.eval()
    episodes = []
    for record in manifest["paths"]:
        if record["partition"] != partition.value:
            continue
        with gzip.open(directory / record["filename"], "rt", encoding="utf-8") as handle:
            path = json.load(handle)
        x, _, _ = build_examples({"paths": [path]}, partition=partition,
            purpose=DataPurpose.STAGE1_TRAIN if partition is Partition.TRAIN
                    else DataPurpose.DEVELOPMENT_EVAL, stride_bars=stride_bars)
        normalized = (x - checkpoint["normalization_mean"]) / checkpoint["normalization_std"]
        with torch.no_grad():
            logits, continuous, _ = model(torch.tensor(normalized, dtype=torch.float32))
            probabilities = torch.softmax(logits, dim=1).numpy()
            continuous = continuous.numpy()
        bars, rows = path["bars"], []
        required = max(WINDOWS.values())
        for offset, end in enumerate(range(required, len(bars), stride_bars)):
            next_end = min(end + stride_bars, len(bars))
            if next_end <= end: continue
            decision, future = bars[end - 1], bars[next_end - 1]
            ranges = []
            for bar_index in range(max(1, end - 14), end):
                bar, previous = bars[bar_index], bars[bar_index - 1]
                ranges.append(max(float(bar["high"]) - float(bar["low"]),
                    abs(float(bar["high"]) - float(previous["close"])),
                    abs(float(bar["low"]) - float(previous["close"]))))
            rows.append({"encoder": np.concatenate((probabilities[offset],
                continuous[offset])).astype(np.float32),
                "mark_price": float(decision["close"]),
                "atr": float(np.mean(ranges)),
                "market_return": float(future["close"]) / float(decision["close"]) - 1,
                "cost_rate": (float(decision["fee_bps"])
                              + float(decision["slippage_bps"])) / 10000.0})
        episodes.append({"path_sha256": path["path_sha256"], "rows": rows})
    if not episodes or any(len(episode["rows"]) < 2 for episode in episodes):
        raise ValueError("authorized PPO episodes are missing")
    return episodes


class BRLAG2PPOEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, episodes: Sequence[dict[str, Any]]) -> None:
        super().__init__()
        if not episodes or any(not row.get("rows") for row in episodes):
            raise ValueError("non-empty prepared episodes are required")
        self.episodes = list(episodes)
        self.action_space = spaces.Discrete(len(INTENTS))
        self.observation_space = spaces.Box(-np.inf, np.inf,
            shape=(len(OBSERVATION_NAMES),), dtype=np.float32)

    def _observation(self) -> np.ndarray:
        encoded = self.rows[self.index]["encoder"]
        remaining = 1.0 if not self.ledger else self.ledger[-1].remaining_risk_budget_fraction / .02
        return np.asarray([*encoded, self.current, (self.peak - self.equity) / self.peak,
                           self.previous_step_return, remaining], dtype=np.float32)

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        selected = int(self.np_random.integers(0, len(self.episodes)))
        episode = self.episodes[selected]
        self.rows = episode["rows"]; self.path_sha256 = episode["path_sha256"]
        self.index = 0; self.equity = self.peak = 1.0; self.current = 0.0
        self.previous_step_return = 0.0; self.stop = None
        self.ledger: list[RiskLedgerEntry] = []; self.trajectory = []
        self.pending_nemt_prior = None
        return self._observation(), {"path_sha256": self.path_sha256,
                                    "mode": "PAPER", "order_permission": False}

    def _requested(self, action: int) -> float:
        intent = INTENTS[action]
        if intent == "PROBE_SHORT": return -.10 if self.current == 0 else self.current
        if intent == "INCREASE_SHORT": return max(self.current - .10, -.50)
        if intent == "REDUCE_SHORT": return min(self.current + .10, 0.0)
        if intent == "EXIT": return 0.0
        return self.current

    def _allocation_for(self, action: int) -> tuple[RiskLedgerEntry, float | None]:
        row = self.rows[self.index]; encoded = row["encoder"]
        maturity, quality = float(encoded[8]), float(encoded[10])
        requested = self._requested(action)
        if (self.peak - self.equity) / self.peak >= .20: requested = 0.0
        mark = row["mark_price"]
        if requested < self.current:
            candidate = mark * (1.05 if self.current == 0 else 1.02)
            if self.stop is not None:
                candidate = max(mark * 1.005, min(candidate, self.stop * .98))
            stop = candidate
        else:
            stop = self.stop
        allocation = allocate_short_risk(BRPMRequest(
            campaign_id=f"ppo-{self.path_sha256[:16]}", equity=self.equity,
            mark_price=mark, current_target_fraction=self.current,
            requested_target_fraction=requested, protective_stop_price=stop,
            cost_rate=row["cost_rate"], trend_maturity=maturity,
            entry_location_quality=quality), self.ledger)
        return allocation, stop

    def action_masks(self) -> np.ndarray:
        """Expose only position-valid and BRPM-feasible intents."""
        if self.current == 0:
            probe, _ = self._allocation_for(INTENTS.index("PROBE_SHORT"))
            return np.asarray([True, probe.decision == "APPROVE_PROBE",
                               False, False, False, False])
        decisions = {}
        for intent in ("HOLD", "INCREASE_SHORT", "REDUCE_SHORT", "EXIT"):
            allocation, _ = self._allocation_for(INTENTS.index(intent))
            decisions[intent] = allocation.decision != "REJECT"
        return np.asarray([False, False, decisions["HOLD"],
            self.current > -.50 and decisions["INCREASE_SHORT"],
            self.current < -.10 and decisions["REDUCE_SHORT"], decisions["EXIT"]])

    def step(self, action: int):
        action = int(action)
        if not 0 <= action < len(INTENTS): raise ValueError("invalid PPO intent")
        if not bool(self.action_masks()[action]):
            raise PermissionError("position-invalid intent must be masked before execution")
        row = self.rows[self.index]; encoded = row["encoder"]
        probabilities, targets = encoded[:6], encoded[6:]
        maturity, remaining, quality, exhaustion, late_risk = targets[2:]
        drawdown_before = (self.peak - self.equity) / self.peak
        requested = self._requested(action)
        allocation, stop = self._allocation_for(action)
        requested = allocation.requested_target_fraction
        self.ledger.append(allocation)
        risk_valid = verify_risk_ledger(self.ledger)
        guard = gate_paper_target(allocation.approved_target_fraction,
            hard_maximum_short_fraction=.50, market_data_fresh=True,
            account_state_certain=True, risk_state_valid=risk_valid)
        target = guard.approved_paper_target_fraction
        if target == 0: stop = None
        elif allocation.decision != "REJECT": self.stop = stop
        transaction_cost = abs(target - self.current) * row["cost_rate"]
        gross_pnl = target * row["market_return"]
        step_return = gross_pnl - transaction_cost
        next_equity = self.equity * max(.05, 1 + step_return)
        next_peak = max(self.peak, next_equity)
        drawdown_after = (next_peak - next_equity) / next_peak
        new_entry = self.current == 0 and target < 0
        add_risk = self.current < 0 and target < self.current
        reduce = target > self.current and target < 0
        exit_position = self.current < 0 and target == 0
        hold = not any((new_entry, add_risk, reduce, exit_position))
        predicted_state = int(np.argmax(probabilities))
        reward_audit = stage2_timing_reward(trend_maturity=float(maturity),
            remaining_potential=float(remaining), entry_location_quality=float(quality),
            feedback_exhaustion=float(exhaustion), late_entry_risk=float(late_risk),
            new_entry=new_entry, hold_existing=hold, add_risk=add_risk, reduce=reduce,
            exit_position=exit_position, unrealized_profit=max(self.equity - 1, 0),
            old_order_invalidated=predicted_state >= 4, stop_tightened=add_risk,
            gross_pnl=gross_pnl, transaction_cost=transaction_cost,
            drawdown_increase=max(drawdown_after - drawdown_before, 0),
            absolute_exposure=abs(target),
            unsupported_trade=(target < self.current and predicted_state < 4),
            contest_exposure=(target < 0 and predicted_state == 2),
            old_direction_after_new_order=(target < 0 and predicted_state == 0),
            delayed_exit=(target < 0 and exhaustion >= .80),
            correct_wait=(target == 0 and predicted_state < 4), timely_reduce=reduce,
            timely_exit=exit_position, risk_released_add=add_risk,
            proposed_target=requested, brpm_target=allocation.approved_target_fraction,
            brgc_target=target, executed_target=target)
        probe_allocation, _ = self._allocation_for(INTENTS.index("PROBE_SHORT"))
        probe_feasible = probe_allocation.decision == "APPROVE_PROBE"
        opportunity_quality = float(quality * remaining * (1 - late_risk))
        counterfactual_probe_return = max(
            .10 * max(-row["market_return"], 0.0) - .10 * row["cost_rate"], 0.0)
        confirmed_opportunity = (self.current == 0 and target == 0 and probe_feasible
            and quality >= .20 and maturity < .70 and counterfactual_probe_return > 0)
        missed_opportunity = (-min(5 * opportunity_quality * counterfactual_probe_return, .02)
                              if confirmed_opportunity else 0.0)
        reward = reward_audit.total + missed_opportunity
        self.trajectory.append({"intent": INTENTS[action], "requested": requested,
            "brpm_decision": allocation.decision, "brpm_target": allocation.approved_target_fraction,
            "brgc_decision": guard.decision, "executed_target": target,
            "missed_opportunity": missed_opportunity,
            "opportunity_quality": opportunity_quality,
            "counterfactual_probe_return": counterfactual_probe_return,
            "nemt_prior_action": None if self.pending_nemt_prior is None
                else self.pending_nemt_prior.action,
            "nemt_candidate_add_notional": 0.0 if self.pending_nemt_prior is None
                else self.pending_nemt_prior.candidate_add_notional,
            "nemt_candidate_protective_stop": None if self.pending_nemt_prior is None
                else self.pending_nemt_prior.candidate_protective_stop,
            "exposed": target < 0, "order_permission": guard.order_permission})
        self.pending_nemt_prior = None
        self.equity, self.peak, self.previous_step_return, self.current = (
            next_equity, next_peak, step_return, target)
        self.index += 1
        terminated = self.index >= len(self.rows)
        observation = np.zeros(len(OBSERVATION_NAMES), dtype=np.float32) \
            if terminated else self._observation()
        return observation, float(reward), terminated, False, {
            "equity": self.equity, "brpm_decision": allocation.decision,
            "brgc_decision": guard.decision, "approved_target": target,
            "mode": "PAPER", "order_permission": False, "automatic_promotion": False}
