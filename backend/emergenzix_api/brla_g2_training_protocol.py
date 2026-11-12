"""Frozen two-stage BRLA-G2 learning constitution and auditable objectives."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-two-stage-training-v1"


class OrderState(StrEnum):
    OLD_ORDER_DOMINANT = "OLD_ORDER_DOMINANT"
    NEW_ORDER_EMERGING = "NEW_ORDER_EMERGING"
    ORDER_CONTEST = "ORDER_CONTEST"
    OLD_ORDER_FAILURE = "OLD_ORDER_FAILURE"
    NEW_ORDER_PROVISIONAL = "NEW_ORDER_PROVISIONAL"
    NEW_ORDER_CONFIRMED = "NEW_ORDER_CONFIRMED"


class DataPurpose(StrEnum):
    STAGE1_TRAIN = "STAGE1_TRAIN"
    STAGE2_TRAIN = "STAGE2_TRAIN"
    DEVELOPMENT_EVAL = "DEVELOPMENT_EVAL"
    FINAL_SEALED_EVAL = "FINAL_SEALED_EVAL"


@dataclass(frozen=True, slots=True)
class Stage1Observation:
    scenario_sha256: str
    as_of: datetime
    feature_end: datetime
    timeframe_feature_sha256: Mapping[str, str]
    partition: Partition

    def __post_init__(self) -> None:
        _aware(self.as_of); _aware(self.feature_end)
        if self.feature_end > self.as_of:
            raise ValueError("stage-one observation cannot contain future features")
        if set(self.timeframe_feature_sha256) != {"15m", "1h", "4h", "1d"}:
            raise ValueError("all four frozen timeframes are required")
        _hashes((self.scenario_sha256, *self.timeframe_feature_sha256.values()))


@dataclass(frozen=True, slots=True)
class Stage1Label:
    as_of: datetime
    label_horizon_end: datetime
    old_order_strength: float
    new_order_strength: float
    state: OrderState
    confidence: float
    generated_with_future_outcomes: bool = True

    def __post_init__(self) -> None:
        _aware(self.as_of); _aware(self.label_horizon_end)
        if self.label_horizon_end <= self.as_of or self.generated_with_future_outcomes is not True:
            raise ValueError("stage-one labels require a strictly future outcome window")
        _unit((self.old_order_strength, self.new_order_strength, self.confidence))


@dataclass(frozen=True, slots=True)
class Stage1Prediction:
    old_order_strength: float
    new_order_strength: float
    state_probabilities: Mapping[OrderState, float]
    confidence: float
    mode: str = "RESEARCH"
    position_permission: bool = False
    order_permission: bool = False

    def __post_init__(self) -> None:
        _unit((self.old_order_strength, self.new_order_strength, self.confidence))
        if set(self.state_probabilities) != set(OrderState):
            raise ValueError("probabilities for all six order states are required")
        _unit(tuple(self.state_probabilities.values()))
        if not math.isclose(sum(self.state_probabilities.values()), 1.0, abs_tol=1e-8):
            raise ValueError("state probabilities must sum to one")
        if self.position_permission or self.order_permission:
            raise ValueError("stage one has no position or order authority")


@dataclass(frozen=True, slots=True)
class Stage2Observation:
    market_observation_sha256: str
    stage1_prediction_sha256: str
    current_position_fraction: float
    current_drawdown: float
    remaining_risk_budget_fraction: float
    partition: Partition

    def __post_init__(self) -> None:
        _hashes((self.market_observation_sha256, self.stage1_prediction_sha256))
        if not -.5 <= self.current_position_fraction <= 0:
            raise ValueError("stage-two position must remain short-only and bounded")
        _unit((self.current_drawdown, self.remaining_risk_budget_fraction))


@dataclass(frozen=True, slots=True)
class Stage1LossWeights:
    old_strength_mse: float = 1.0
    new_strength_mse: float = 1.0
    state_nll: float = 1.5
    confidence_brier: float = .5
    order_gap_mse: float = .5


@dataclass(frozen=True, slots=True)
class Stage2RewardWeights:
    gross_pnl: float = 1.0
    transaction_cost: float = 1.0
    drawdown_increase: float = 1.0
    exposure: float = .001
    unsupported_trade: float = .01
    contest_exposure: float = .01
    old_direction_after_new_order: float = .03
    delayed_exit: float = .02
    correct_wait: float = .001
    timely_reduce: float = .003
    timely_exit: float = .005
    risk_released_add: float = .002


STAGE1_WEIGHTS = Stage1LossWeights()
STAGE2_WEIGHTS = Stage2RewardWeights()
HARD_CONSTRAINTS = (
    "VALID_STOP_REQUIRED", "RISK_MUST_FALL_BEFORE_ADD", "LOSS_ADD_FORBIDDEN",
    "PORTFOLIO_BUDGET_REQUIRED", "FRESH_DATA_REQUIRED",
    "CERTAIN_ACCOUNT_STATE_REQUIRED", "BRPM_REQUIRED", "BRGC_REQUIRED",
    "LIVE_DISABLED",
)


@dataclass(frozen=True, slots=True)
class ObjectiveAudit:
    protocol: str
    objective: str
    components: Mapping[str, float]
    total: float
    audit_sha256: str
    automatic_promotion: bool = False
    order_permission: bool = False


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


def _unit(values) -> None:
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError("values must be finite and in [0, 1]")


def _hashes(values) -> None:
    if any(len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
           for value in values):
        raise ValueError("content fingerprints must be lowercase SHA-256")


def _audit(objective: str, components: Mapping[str, float]) -> ObjectiveAudit:
    total = math.fsum(components.values())
    payload = {"protocol": PROTOCOL, "objective": objective,
               "components": dict(components), "total": total,
               "automatic_promotion": False, "order_permission": False}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return ObjectiveAudit(PROTOCOL, objective, dict(components), total, digest)


def stage1_loss(prediction: Stage1Prediction, label: Stage1Label,
                weights: Stage1LossWeights = STAGE1_WEIGHTS) -> ObjectiveAudit:
    probability = max(float(prediction.state_probabilities[label.state]), 1e-12)
    components = {
        "old_strength_mse": weights.old_strength_mse
            * (prediction.old_order_strength - label.old_order_strength) ** 2,
        "new_strength_mse": weights.new_strength_mse
            * (prediction.new_order_strength - label.new_order_strength) ** 2,
        "state_nll": weights.state_nll * -math.log(probability),
        "confidence_brier": weights.confidence_brier
            * (prediction.confidence - label.confidence) ** 2,
        "order_gap_mse": weights.order_gap_mse * (
            (prediction.new_order_strength - prediction.old_order_strength)
            - (label.new_order_strength - label.old_order_strength)) ** 2,
    }
    return _audit("MINIMIZE_STAGE1_LOSS", components)


def stage2_reward(*, gross_pnl: float, transaction_cost: float,
                  drawdown_increase: float, absolute_exposure: float,
                  unsupported_trade: bool, contest_exposure: bool,
                  old_direction_after_new_order: bool, delayed_exit: bool,
                  correct_wait: bool, timely_reduce: bool, timely_exit: bool,
                  risk_released_add: bool,
                  proposed_target: float, brpm_target: float, brgc_target: float,
                  executed_target: float,
                  weights: Stage2RewardWeights = STAGE2_WEIGHTS) -> ObjectiveAudit:
    numeric = (gross_pnl, transaction_cost, drawdown_increase, absolute_exposure,
               proposed_target, brpm_target, brgc_target, executed_target)
    if any(not math.isfinite(value) for value in numeric):
        raise ValueError("reward inputs must be finite")
    if transaction_cost < 0 or drawdown_increase < 0 or not 0 <= absolute_exposure <= .5:
        raise ValueError("cost, drawdown and exposure inputs are invalid")
    if executed_target != brgc_target:
        raise PermissionError("executed target must equal the BRGC-approved target")
    if abs(brgc_target) > abs(brpm_target) or abs(brpm_target) > .5:
        raise PermissionError("BRGC/BRPM authority chain is invalid")
    components = {
        "gross_pnl": weights.gross_pnl * gross_pnl,
        "transaction_cost": -weights.transaction_cost * transaction_cost,
        "drawdown_increase": -weights.drawdown_increase * drawdown_increase,
        "exposure": -weights.exposure * absolute_exposure,
        "unsupported_trade": -weights.unsupported_trade * int(unsupported_trade),
        "contest_exposure": -weights.contest_exposure * int(contest_exposure),
        "old_direction_after_new_order": -weights.old_direction_after_new_order
            * int(old_direction_after_new_order),
        "delayed_exit": -weights.delayed_exit * int(delayed_exit),
        "correct_wait": weights.correct_wait * int(correct_wait),
        "timely_reduce": weights.timely_reduce * int(timely_reduce),
        "timely_exit": weights.timely_exit * int(timely_exit),
        "risk_released_add": weights.risk_released_add * int(risk_released_add),
    }
    return _audit("MAXIMIZE_STAGE2_REWARD", components)


def authorize_data_use(partition: Partition, purpose: DataPurpose, *,
                       model_frozen: bool = False,
                       sealed_access_authorized: bool = False) -> None:
    allowed = {
        DataPurpose.STAGE1_TRAIN: Partition.TRAIN,
        DataPurpose.STAGE2_TRAIN: Partition.TRAIN,
        DataPurpose.DEVELOPMENT_EVAL: Partition.DEVELOPMENT,
        DataPurpose.FINAL_SEALED_EVAL: Partition.SEALED_TEST,
    }
    if allowed[purpose] is not partition:
        raise PermissionError("data partition is not authorized for this purpose")
    if purpose is DataPurpose.FINAL_SEALED_EVAL and not (
            model_frozen and sealed_access_authorized):
        raise PermissionError("sealed evaluation requires a frozen model and explicit access")


def protocol_manifest() -> dict[str, object]:
    payload = {"protocol": PROTOCOL, "stage1_loss_weights": asdict(STAGE1_WEIGHTS),
        "stage2_reward_weights": asdict(STAGE2_WEIGHTS),
        "hard_constraints": HARD_CONSTRAINTS,
        "stage1_position_permission": False, "stage1_order_permission": False,
        "stage2_requires_brpm": True, "stage2_requires_brgc": True,
        "automatic_promotion": False, "order_permission": False}
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
