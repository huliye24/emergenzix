"""Tests for the NEMT v5.2.6 incremental-information ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emergenzix_api.nemt_strategy import v526_incremental_information_cli as viv
from emergenzix_api.nemt_strategy.nemt_momentum_v2 import MomentumState
from emergenzix_api.schemas import Bar

ZONE = ZoneInfo("Asia/Shanghai")
T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
WINDOW = (T0, T0 + timedelta(days=400))


def _series(closes: list[float]) -> list[Bar]:
    out, previous = [], closes[0]
    for index, close in enumerate(closes):
        open_price = previous if index else close
        out.append(Bar(timestamp=T0 + timedelta(hours=4) * index, open=open_price,
                       high=max(open_price, close), low=min(open_price, close),
                       close=close, volume=1.0))
        previous = close
    return out


def _state(phase: str, psi: float):
    return lambda closes, config=None: MomentumState(psi, 0.9, phase, 90)


def _ledger(closes: list[float], state_fn):
    return viv.build_ledger(bars=_series(closes), zone=ZONE, start=WINDOW[0],
                            end=WINDOW[1], state_fn=state_fn)


def test_synthetic_validation_block_passes() -> None:
    checks = viv.synthetic_validation()
    assert len(checks) == 7
    assert all(check["passed"] for check in checks), checks


def test_gate_labels_every_breakout() -> None:
    closes = [100.0] * 340 + [120.0, 120.0]
    permitted = _ledger(closes, _state("critical_up", 1.0))
    refused = _ledger(closes, _state("stable", 0.0))
    inverted = _ledger(closes, _state("critical_down", -1.0))

    assert [e["g"] for e in permitted] == [1]
    assert [e["g"] for e in refused] == [0]
    assert [e["g"] for e in inverted] == [0]
    assert permitted[0]["nemt_direction"] == 1
    assert inverted[0]["nemt_direction"] == -1


def test_short_breakout_is_detected() -> None:
    closes = [100.0] * 340 + [80.0, 80.0]
    events = _ledger(closes, _state("critical_down", -1.0))
    assert len(events) == 1
    assert events[0]["breakout_direction"] == -1
    assert events[0]["g"] == 1
    assert events[0]["side"] == "SHORT"


def test_events_before_warmup_are_not_enumerated() -> None:
    assert _ledger([100.0] * 50, _state("critical_up", 1.0)) == []


def test_dedupe_keeps_first_event_per_direction() -> None:
    events = [
        {"decision_index": index, "breakout_direction": 1}
        for index in (340, 344, 352, 353, 400)
    ]
    events.append({"decision_index": 346, "breakout_direction": -1})
    kept = viv.dedupe_events(events, gap=12)
    assert [e["decision_index"] for e in kept] == [340, 346, 352, 400]


def test_delta_r_requires_both_groups() -> None:
    events = [{"g": 1, "r_net": 0.5, "status": "CLOSED", "month": "2025-01"}] * 20
    result = viv.delta_r(events)
    assert result["status"] == "INCONCLUSIVE"
    assert result["reason"] == "insufficient_events_in_a_group"
    assert result["delta_r"] is None


def test_delta_r_point_estimate_and_blocks() -> None:
    events = []
    for month in ("2025-01", "2025-02", "2025-03", "2025-04"):
        events.extend({"g": 1, "r_net": 1.0, "status": "CLOSED", "month": month} for _ in range(6))
        events.extend({"g": 0, "r_net": -1.0, "status": "CLOSED", "month": month} for _ in range(6))
    result = viv.delta_r(events, bootstrap_rounds=200)
    assert result["status"] == "ESTIMATED"
    assert result["delta_r"] == pytest.approx(2.0)
    assert result["ci95"][0] == pytest.approx(2.0)
    assert result["ci_excludes_zero_positive"] is True


def test_censored_events_are_excluded_from_delta_r() -> None:
    events = [{"g": 1, "r_net": 1.0, "status": "CLOSED", "month": m}
              for m in ("2025-01", "2025-02", "2025-03") for _ in range(6)]
    events += [{"g": 0, "r_net": -1.0, "status": "CLOSED", "month": m}
               for m in ("2025-01", "2025-02", "2025-03") for _ in range(6)]
    events += [{"g": 0, "r_net": None, "status": "CENSORED", "month": "2025-01"}] * 5
    result = viv.delta_r(events, bootstrap_rounds=100)
    assert result["censored_events"] == 5
    assert result["g0_events"] == 18
    assert result["delta_r"] == pytest.approx(2.0)
