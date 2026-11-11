"""Versioned, fail-closed contracts for the BRMG -> BRLA -> BRPM -> BRGC pipeline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "emergenzix-reflexive-pipeline-v1"
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractMeta(StrictContract):
    contract_version: Literal["emergenzix-reflexive-pipeline-v1"] = CONTRACT_VERSION
    trace_id: Annotated[str, Field(min_length=1, max_length=128)]
    input_sha256: Sha256
    generated_at: datetime
    mode: Literal["PAPER"] = "PAPER"
    automatic_promotion: Literal[False] = False
    order_permission: Literal[False] = False

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


class BRMGInput(ContractMeta):
    module: Literal["BRMG"] = "BRMG"
    dataset_id: str
    scenario_family: Literal["BEAR_CONTINUATION"]
    horizon_bars: Annotated[int, Field(ge=2, le=1000)]
    seed: int


class MarketPath(StrictContract):
    path_id: str
    asset_id: str
    probability: Fraction
    bar_count: Annotated[int, Field(ge=2)]
    path_sha256: Sha256


class BRMGOutput(ContractMeta):
    module: Literal["BRMG"] = "BRMG"
    paths: Annotated[list[MarketPath], Field(min_length=1)]
    generator_id: str


class BRLAInput(ContractMeta):
    module: Literal["BRLA"] = "BRLA"
    completed_market_snapshot_id: str
    current_position_fraction: Annotated[float, Field(ge=-0.5, le=0.0)]
    current_drawdown: Fraction
    available_feature_names: Annotated[list[str], Field(min_length=1)]


class IntentAction(StrEnum):
    WAIT = "WAIT"
    PROBE_SHORT = "PROBE_SHORT"
    HOLD = "HOLD"
    INCREASE_SHORT = "INCREASE_SHORT"
    REDUCE_SHORT = "REDUCE_SHORT"
    EXIT = "EXIT"


class BRLAOutput(ContractMeta):
    module: Literal["BRLA"] = "BRLA"
    intent: IntentAction
    confidence: Fraction
    reflexive_stage: str
    evidence: Annotated[list[str], Field(min_length=1, max_length=32)]
    invalidation_condition: str


class BRPMInput(ContractMeta):
    module: Literal["BRPM"] = "BRPM"
    intent_sha256: Sha256
    current_position_fraction: Annotated[float, Field(ge=-0.5, le=0.0)]
    current_drawdown: Fraction
    previous_step_return: float
    hard_maximum_short_fraction: Annotated[float, Field(gt=0.0, le=0.5)] = 0.5
    stop_price: Annotated[float | None, Field(gt=0.0)] = None


class BRPMOutput(ContractMeta):
    module: Literal["BRPM"] = "BRPM"
    hard_maximum_short_fraction: Annotated[float, Field(gt=0.0, le=0.5)]
    risk_budget_fraction: Annotated[float, Field(ge=0.0, le=0.5)]
    proposed_target_fraction: Annotated[float, Field(ge=-0.5, le=0.0)]
    estimated_worst_loss_fraction: Fraction
    stop_price: Annotated[float | None, Field(gt=0.0)] = None
    allocation_reason: str

    @model_validator(mode="after")
    def target_must_fit_budget(self) -> "BRPMOutput":
        if self.risk_budget_fraction > self.hard_maximum_short_fraction:
            raise ValueError("risk budget exceeds hard maximum")
        if abs(self.proposed_target_fraction) > self.risk_budget_fraction:
            raise ValueError("proposed target exceeds allocated risk budget")
        return self


class GuardDecision(StrEnum):
    REJECT = "REJECT"
    LIMIT = "LIMIT"
    ALLOW_PAPER_TARGET = "ALLOW_PAPER_TARGET"
    FORCE_EXIT = "FORCE_EXIT"


class BRGCInput(ContractMeta):
    module: Literal["BRGC"] = "BRGC"
    intent_sha256: Sha256
    allocation_sha256: Sha256
    market_data_fresh: bool
    account_state_certain: bool
    stop_state_valid: bool
    requested_target_fraction: Annotated[float, Field(ge=-0.5, le=0.0)]
    hard_maximum_short_fraction: Annotated[float, Field(gt=0.0, le=0.5)] = 0.5

    @model_validator(mode="after")
    def request_must_fit_hard_maximum(self) -> "BRGCInput":
        if abs(self.requested_target_fraction) > self.hard_maximum_short_fraction:
            raise ValueError("requested target exceeds hard maximum")
        return self


class BRGCOutput(ContractMeta):
    module: Literal["BRGC"] = "BRGC"
    decision: GuardDecision
    hard_maximum_short_fraction: Annotated[float, Field(gt=0.0, le=0.5)]
    approved_paper_target_fraction: Annotated[float, Field(ge=-0.5, le=0.0)]
    reason_codes: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode="after")
    def rejected_decisions_are_flat(self) -> "BRGCOutput":
        if abs(self.approved_paper_target_fraction) > self.hard_maximum_short_fraction:
            raise ValueError("approved target exceeds hard maximum")
        if self.decision in {GuardDecision.REJECT, GuardDecision.FORCE_EXIT}:
            if self.approved_paper_target_fraction != 0:
                raise ValueError("rejected or forced-exit decisions must be flat")
        return self


CONTRACT_MODELS = {
    "brmg-input": BRMGInput, "brmg-output": BRMGOutput,
    "brla-input": BRLAInput, "brla-output": BRLAOutput,
    "brpm-input": BRPMInput, "brpm-output": BRPMOutput,
    "brgc-input": BRGCInput, "brgc-output": BRGCOutput,
}


def contract_schema_bundle() -> dict[str, object]:
    schemas = {name: model.model_json_schema() for name, model in CONTRACT_MODELS.items()}
    canonical = json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
    return {"contract_version": CONTRACT_VERSION, "schemas": schemas,
            "schemas_sha256": hashlib.sha256(canonical).hexdigest()}


def write_contract_schema_bundle(output: Path) -> None:
    if output.exists():
        raise FileExistsError("contract bundle already exists")
    output.write_text(json.dumps(contract_schema_bundle(), indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
