import pytest

from emergenzix_api.brla_nemt_v4_prior import (
    NEMTV4PriorRequest, evaluate_nemt_v4_prior, nemt_v4_prior_manifest,
)
from emergenzix_api.nemt_strategy.nemt_pyramiding_v4 import PyramidingV4Config


def short_request(**overrides):
    values = dict(side=-1, initial_entry=100, initial_stop=105,
        initial_notional=1000, current_quantity=10, total_cost=1000,
        protective_stop=102, current_price=94, atr=2, equity=10_000,
        add_count=0, same_direction_confirmed=True, best_price=94)
    values.update(overrides)
    return NEMTV4PriorRequest(**values)


def test_existing_nemt_mother_line_proposes_decreasing_add_without_authority():
    advice = evaluate_nemt_v4_prior(short_request())
    assert advice.action == "DECREASING_ADD_CANDIDATE"
    assert advice.candidate_add_notional == 500
    assert advice.add_index == 0
    assert advice.brpm_required is True
    assert advice.brgc_required is True
    assert advice.position_permission is False
    assert advice.order_permission is False


def test_unconfirmed_direction_refuses_add_but_can_tighten_protection():
    advice = evaluate_nemt_v4_prior(short_request(same_direction_confirmed=False))
    assert advice.candidate_add_notional == 0
    assert advice.action in {"HOLD", "TIGHTEN_STOP"}


def test_trailing_only_mother_variant_never_proposes_add():
    advice = evaluate_nemt_v4_prior(short_request(),
        config=PyramidingV4Config(mode="v4_c_trailing_only"))
    assert advice.candidate_add_notional == 0
    assert advice.action == "TIGHTEN_STOP"


def test_prior_rejects_missing_existing_position():
    with pytest.raises(ValueError, match="positive existing-position"):
        short_request(current_quantity=0)


def test_prior_manifest_freezes_permission_boundary():
    manifest = nemt_v4_prior_manifest()
    assert manifest["scope"] == "EXISTING_POSITION_ONLY"
    assert manifest["cannot_generate_entry_direction"] is True
    assert manifest["brpm_required"] is True
    assert manifest["order_permission"] is False
    assert len(manifest["protocol_sha256"]) == 64
