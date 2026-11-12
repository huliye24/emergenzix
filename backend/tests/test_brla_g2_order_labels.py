from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.brla_g2_multitimeframe import build_multitimeframe_observation
from emergenzix_api.brla_g2_order_labels import (
    CHALLENGER_FLOOR, FUTURE_BAR_COUNT, OrderState, as_stage1_label,
    label_order_competition, label_protocol_manifest,
)
from emergenzix_api.brmg_scenarios import Partition

AS_OF = datetime(2026, 7, 1, tzinfo=timezone.utc)


def history():
    start = AS_OF - timedelta(days=180)
    rows, price = [], 200.0
    for index in range(180 * 96):
        opening = price; price *= .99994
        rows.append({"timestamp": (start + timedelta(minutes=15 * index)).isoformat(),
            "open": opening, "high": max(opening, price) * 1.0002,
            "low": min(opening, price) * .9998, "close": price,
            "base_volume": 1000, "is_closed": True, "is_imputed": False})
    return rows


def future(pattern):
    rows, price = [], history()[-1]["close"]
    for index in range(FUTURE_BAR_COUNT):
        opening = price; rate = pattern(index)
        price *= 1 + rate
        rows.append({"timestamp": (AS_OF + timedelta(minutes=15 * index)).isoformat(),
            "open": opening, "high": max(opening, price) * 1.0003,
            "low": min(opening, price) * .9997, "close": price,
            "base_volume": 1200, "is_closed": True, "is_imputed": False})
    return rows


def observation():
    return build_multitimeframe_observation(history(), as_of=AS_OF)


def test_challenger_strength_is_never_zero_even_when_old_bear_order_continues():
    label = label_order_competition(observation(), future(lambda _: -.0005),
        scenario_sha256="a" * 64, partition=Partition.TRAIN)
    assert label.new_order_strength >= CHALLENGER_FLOOR
    assert label.old_order_strength > label.new_order_strength
    assert label.old_direction.value == "BEARISH"


def test_sustained_counter_order_becomes_stronger_than_old_order():
    label = label_order_competition(observation(), future(lambda _: .0012),
        scenario_sha256="b" * 64, partition=Partition.TRAIN)
    assert label.new_order_strength > label.old_order_strength
    assert label.state in {OrderState.NEW_ORDER_PROVISIONAL, OrderState.NEW_ORDER_CONFIRMED}
    assert label.challenger_persistence >= .60


def test_failed_counter_move_does_not_become_confirmed_new_order():
    label = label_order_competition(observation(),
        future(lambda index: .001 if index < FUTURE_BAR_COUNT // 3 else -.001),
        scenario_sha256="c" * 64, partition=Partition.DEVELOPMENT)
    assert label.state is not OrderState.NEW_ORDER_CONFIRMED


def test_future_changes_label_but_never_mutates_observation():
    observed = observation(); before = observed.observation_sha256
    down = label_order_competition(observed, future(lambda _: -.0005),
        scenario_sha256="d" * 64, partition=Partition.TRAIN)
    up = label_order_competition(observed, future(lambda _: .001),
        scenario_sha256="d" * 64, partition=Partition.TRAIN)
    assert down.label_sha256 != up.label_sha256
    assert observed.observation_sha256 == before
    assert down.position_permission is False and down.order_permission is False


def test_incomplete_or_imputed_future_window_fails_closed():
    with pytest.raises(ValueError, match="exactly seven days"):
        label_order_competition(observation(), future(lambda _: 0)[:-1],
            scenario_sha256="e" * 64, partition=Partition.TRAIN)
    rows = future(lambda _: 0); rows[10]["is_imputed"] = True
    with pytest.raises(ValueError, match="complete"):
        label_order_competition(observation(), rows,
            scenario_sha256="e" * 64, partition=Partition.TRAIN)


def test_label_converts_to_stage_one_target_and_protocol_is_hashed():
    label = label_order_competition(observation(), future(lambda _: -.0005),
        scenario_sha256="f" * 64, partition=Partition.SEALED_TEST)
    target = as_stage1_label(label)
    assert target.generated_with_future_outcomes is True
    assert target.label_horizon_end > target.as_of
    manifest = label_protocol_manifest()
    assert manifest["future_use"] == "STAGE1_LABEL_ONLY"
    assert len(manifest["protocol_sha256"]) == 64
