from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from emergenzix_api.reflexive_pipeline_contracts import (
    BRGCOutput, BRLAOutput, BRMGOutput, BRPMOutput, GuardDecision,
    IntentAction, contract_schema_bundle,
)

META = {"trace_id": "trace-1", "input_sha256": "a" * 64,
        "generated_at": datetime(2026, 11, 11, tzinfo=timezone.utc)}


def test_all_eight_strict_json_contracts_are_frozen_and_hashed():
    bundle = contract_schema_bundle()
    assert bundle["contract_version"] == "emergenzix-reflexive-pipeline-v1"
    assert len(bundle["schemas"]) == 8
    assert len(bundle["schemas_sha256"]) == 64
    assert all(schema["additionalProperties"] is False
               for schema in bundle["schemas"].values())


@pytest.mark.parametrize("model,payload", [
    (BRMGOutput, {**META, "paths": [], "generator_id": "chronos", "order_id": "x"}),
    (BRLAOutput, {**META, "intent": IntentAction.WAIT, "confidence": .5,
                  "reflexive_stage": "WAIT", "evidence": ["completed bars"],
                  "invalidation_condition": "none", "final_position": -.5}),
    (BRPMOutput, {**META, "risk_budget_fraction": .1, "proposed_target_fraction": -.1,
                  "hard_maximum_short_fraction": .1,
                  "estimated_worst_loss_fraction": .01, "allocation_reason": "probe",
                  "order_permission": True}),
])
def test_upstream_modules_cannot_smuggle_forbidden_authority(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_brpm_cannot_exceed_its_own_allocated_budget():
    with pytest.raises(ValidationError, match="exceeds allocated risk budget"):
        BRPMOutput.model_validate({**META, "risk_budget_fraction": .1,
            "hard_maximum_short_fraction": .5,
            "proposed_target_fraction": -.25, "estimated_worst_loss_fraction": .02,
            "allocation_reason": "too large"})


def test_brpm_cannot_raise_a_lower_runtime_hard_limit():
    with pytest.raises(ValidationError, match="risk budget exceeds hard maximum"):
        BRPMOutput.model_validate({**META, "hard_maximum_short_fraction": .1,
            "risk_budget_fraction": .25, "proposed_target_fraction": -.1,
            "estimated_worst_loss_fraction": .02, "allocation_reason": "too large"})


def test_brgc_is_final_paper_gate_and_fails_flat_on_rejection():
    with pytest.raises(ValidationError, match="must be flat"):
        BRGCOutput.model_validate({**META, "decision": GuardDecision.REJECT,
            "hard_maximum_short_fraction": .5,
            "approved_paper_target_fraction": -.1, "reason_codes": ["STALE_DATA"]})
    accepted = BRGCOutput.model_validate({**META,
        "decision": GuardDecision.ALLOW_PAPER_TARGET,
        "hard_maximum_short_fraction": .1,
        "approved_paper_target_fraction": -.1, "reason_codes": ["ALL_CHECKS_PASS"]})
    assert accepted.mode == "PAPER"
    assert accepted.order_permission is False
    assert accepted.automatic_promotion is False


def test_brgc_cannot_approve_above_runtime_hard_limit():
    with pytest.raises(ValidationError, match="approved target exceeds hard maximum"):
        BRGCOutput.model_validate({**META,
            "decision": GuardDecision.ALLOW_PAPER_TARGET,
            "hard_maximum_short_fraction": .1,
            "approved_paper_target_fraction": -.25,
            "reason_codes": ["BAD_APPROVAL"]})
