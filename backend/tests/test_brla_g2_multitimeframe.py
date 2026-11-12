from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.brla_g2_multitimeframe import (
    WINDOW_BARS, build_multitimeframe_observation, observation_protocol_manifest,
    stage1_feature_fingerprints,
)

AS_OF = datetime(2026, 7, 1, tzinfo=timezone.utc)


def source_bars(*, future=0):
    start = AS_OF - timedelta(days=180)
    count = 180 * 96 + future
    rows, price = [], 100.0
    for index in range(count):
        timestamp = start + timedelta(minutes=15 * index)
        opening = price
        price *= 1 + ((index % 7) - 3) * .00001
        rows.append({"timestamp": timestamp.isoformat(), "open": opening,
            "high": max(opening, price) * 1.0001,
            "low": min(opening, price) * .9999, "close": price,
            "base_volume": 1000 + index % 20,
            "is_closed": True, "is_imputed": False})
    return rows


def test_four_timeframes_have_frozen_sizes_and_causal_end_times():
    result = build_multitimeframe_observation(source_bars(), as_of=AS_OF)
    assert set(result.windows) == {"15m", "1h", "4h", "1d"}
    for timeframe, count in WINDOW_BARS.items():
        assert len(result.windows[timeframe].bars) == count
        assert result.windows[timeframe].bars[-1].close_time <= result.as_of
    assert result.position_permission is False and result.order_permission is False


def test_future_bars_cannot_change_observation_fingerprint():
    causal = source_bars()
    with_future = source_bars(future=4)
    for row in with_future[-4:]:
        row["close"] = -1
        row["is_closed"] = False
    first = build_multitimeframe_observation(causal, as_of=AS_OF)
    second = build_multitimeframe_observation(with_future, as_of=AS_OF)
    assert first.observation_sha256 == second.observation_sha256


def test_incomplete_higher_timeframe_is_excluded():
    decision = AS_OF + timedelta(minutes=15)
    rows = source_bars(future=1)
    result = build_multitimeframe_observation(rows, as_of=decision)
    assert result.windows["15m"].bars[-1].close_time == decision.isoformat()
    assert result.windows["1h"].bars[-1].close_time == AS_OF.isoformat()
    assert result.windows["4h"].bars[-1].close_time == AS_OF.isoformat()
    assert result.windows["1d"].bars[-1].close_time == AS_OF.isoformat()


@pytest.mark.parametrize("mutation", ["missing", "imputed", "open", "misaligned"])
def test_bad_source_data_fails_closed(mutation):
    rows = source_bars()
    if mutation == "missing":
        rows.pop(100)
    elif mutation == "imputed":
        rows[100]["is_imputed"] = True
    elif mutation == "open":
        rows[100]["is_closed"] = False
    else:
        rows[100]["timestamp"] = (datetime.fromisoformat(rows[100]["timestamp"])
            + timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError):
        build_multitimeframe_observation(rows, as_of=AS_OF)


def test_window_fingerprints_are_ready_for_stage_one_only():
    result = build_multitimeframe_observation(source_bars(), as_of=AS_OF)
    fingerprints = stage1_feature_fingerprints(result)
    assert set(fingerprints) == {"15m", "1h", "4h", "1d"}
    assert all(len(value) == 64 for value in fingerprints.values())
    manifest = observation_protocol_manifest()
    assert manifest["future_fields_present"] is False
    assert manifest["gap_policy"] == "FAIL_CLOSED"
    assert len(manifest["protocol_sha256"]) == 64
