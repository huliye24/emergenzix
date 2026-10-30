"""Tests for the NEMT v5.2.7 entropy-versus-psi ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emergenzix_api.nemt_strategy import v527_entropy_cli as ven
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


def _ledger(closes: list[float], psi: float, entropy: float):
    return ven.build_psi_ledger(
        bars=_series(closes), zone=ZONE, start=WINDOW[0], end=WINDOW[1],
        state_fn=lambda c, config=None: MomentumState(psi, entropy, "stable", 90),
    )


def _event(index: int, m: int, f: int, r: float | None, month: str, status: str = "CLOSED"):
    return {"decision_index": index, "month": month, "m": m, "f": f,
            "r_net": r, "status": status, "breakout_direction": 1,
            "psi": 1.0, "entropy": 0.9, "atr_over_price": 0.01}


def test_synthetic_validation_block_passes() -> None:
    checks = ven.synthetic_validation()
    assert len(checks) == 8
    assert all(c["passed"] for c in checks), checks


def test_psi_gate_ignores_entropy_but_entropy_flag_records_it() -> None:
    closes = [100.0] * 340 + [120.0, 120.0]
    high = _ledger(closes, psi=1.0, entropy=0.9)
    low = _ledger(closes, psi=1.0, entropy=0.2)

    assert [e["m"] for e in high] == [1] and [e["f"] for e in high] == [1]
    assert [e["m"] for e in low] == [1] and [e["f"] for e in low] == [0]


def test_weak_or_opposite_psi_is_not_a_candidate() -> None:
    closes = [100.0] * 340 + [120.0, 120.0]
    assert [e["m"] for e in _ledger(closes, psi=0.4, entropy=0.9)] == [0]
    assert [e["m"] for e in _ledger(closes, psi=-1.0, entropy=0.9)] == [0]


def test_entropy_threshold_boundary_is_exclusive() -> None:
    closes = [100.0] * 340 + [120.0, 120.0]
    assert [e["f"] for e in _ledger(closes, psi=1.0, entropy=0.7)] == [0]


def test_delta_s_reports_no_filter_variation() -> None:
    events = [_event(index, 1, 1, 0.5, "2025-01") for index in range(20)]
    result = ven.delta_s(events)
    assert result["status"] == "NO_FILTER_VARIATION"
    assert result["delta_s_raw"] is None


def test_delta_s_inconclusive_when_a_group_is_small() -> None:
    events = [_event(index, 1, 1, 0.5, "2025-01") for index in range(20)]
    events += [_event(100 + index, 1, 0, -0.5, "2025-01") for index in range(5)]
    result = ven.delta_s(events)
    assert result["status"] == "INCONCLUSIVE"
    assert result["reason"] == "insufficient_closed_events_in_a_group"


def test_delta_s_point_estimate_and_overlap() -> None:
    events = []
    for month in ("2025-01", "2025-02", "2025-03"):
        events += [_event(index, 1, 1, 1.0, month) for index in range(6)]
        events += [_event(100 + index, 1, 0, -1.0, month) for index in range(6)]
    result = ven.delta_s(events, bootstrap_rounds=200)
    assert result["status"] == "ESTIMATED"
    assert result["delta_s_raw"] == pytest.approx(2.0)
    assert result["ci95"][0] == pytest.approx(2.0)
    assert result["ci_excludes_zero_positive"] is True
    assert result["months_with_both_groups"] == 3


def test_delta_s_ignores_non_candidates_and_censored() -> None:
    events = []
    for month in ("2025-01", "2025-02", "2025-03"):
        events += [_event(index, 1, 1, 1.0, month) for index in range(6)]
        events += [_event(100 + index, 1, 0, -1.0, month) for index in range(6)]
    events += [_event(200, 0, 1, 99.0, "2025-01")]          # not an M candidate
    events += [_event(300, 1, 1, None, "2025-01", "CENSORED")]
    result = ven.delta_s(events, bootstrap_rounds=100)
    assert result["m_closed"] == 36
    assert result["m_censored"] == 1
    assert result["delta_s_raw"] == pytest.approx(2.0)
