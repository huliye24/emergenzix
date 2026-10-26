"""Tests for the S-003.1 rolling-equity and tranche ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from emergenzix_api.nemt_strategy import s0031_ledger_cli as ledger

ZONE = ZoneInfo("Asia/Shanghai")


def test_synthetic_cashflow_block_passes() -> None:
    checks = ledger.synthetic_cashflow_tests()
    assert len(checks) == 9
    assert all(check["passed"] for check in checks), checks


def test_plans_are_single_risk_slice_total() -> None:
    for key, plan in ledger.PLANS.items():
        assert abs(sum(plan["slices"]) - 1.0) <= 1e-12, key
    assert ledger.PLANS["R0"]["slices"] == (1.0,)
    assert ledger.PLANS["F0"]["sizing"] == "fixed"
    assert ledger.PLANS["R0"]["sizing"] == "rolling"
    assert ledger.PLANS["T3"]["slices"] == (0.5, 0.25, 0.25)
    assert ledger.PLANS["T2"]["slices"] == (0.6, 0.4)


def test_tranche_quantity_keeps_total_risk_equal_to_budget() -> None:
    """Quantity from slice_risk / distance must sum to the plan budget."""
    budget, d1 = 1.0, 5.0
    first = 100.0
    stop = first - d1
    slices = ledger.PLANS["T3"]["slices"]
    entries = [first, first + d1, first + 2 * d1]
    total_risk = 0.0
    for slice_risk, entry in zip(slices, entries):
        quantity = (slice_risk * budget) / abs(entry - stop)
        total_risk += quantity * abs(entry - stop)
    assert total_risk == pytest.approx(budget)


def _bars(closes: list[float]) -> list:
    from emergenzix_api.schemas import Bar

    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    out, previous = [], closes[0]
    for index, close in enumerate(closes):
        open_price = previous if index else close
        out.append(Bar(timestamp=t0 + timedelta(hours=4) * index, open=open_price,
                       high=max(open_price, close), low=min(open_price, close),
                       close=close, volume=1.0))
        previous = close
    return out


def test_flat_market_produces_no_trades_and_flat_ledger() -> None:
    bars = _bars([100.0] * 400)
    for key in ("R0", "F0", "T3", "T2"):
        result = ledger.run_plan(
            bars=bars, zone=ZONE, start=bars[0].timestamp, end=bars[-1].timestamp,
            plan=ledger.PLANS[key], capital=100.0,
        )
        assert result["metrics"]["trades"] == 0
        assert result["metrics"]["final_equity"] == pytest.approx(100.0)
        assert all(result["ledger_checks"].values())


def test_ledger_exports_required_fields() -> None:
    bars = _bars([100.0] * 400)
    result = ledger.run_plan(
        bars=bars, zone=ZONE, start=bars[0].timestamp, end=bars[-1].timestamp,
        plan=ledger.PLANS["R0"], capital=100.0,
    )
    point = result["equity_curve"][0]
    for field in ("equity", "cash", "locked_notional", "realized_net", "unrealized",
                  "fees_paid", "slippage_cost", "external_flow"):
        assert field in point, field
    assert point["external_flow"] == 0.0


def test_arm_c_matches_t3_except_for_the_stop_rule() -> None:
    """C1 is arm C: identical to T3 apart from the breakeven stop rule."""
    t3 = ledger.PLANS["T3"]
    c1 = ledger.PLANS["C1"]
    assert c1["slices"] == t3["slices"]
    assert c1["sizing"] == t3["sizing"]
    assert c1.get("breakeven_after") == 2
    assert "breakeven_after" not in t3


def test_breakeven_stop_only_tightens() -> None:
    side = 1
    fee_rate, slippage_rate = 0.0005, 0.0002
    avg_entry = 100.0
    stop = 95.0
    cost_buffer = 2 * fee_rate + 2 * slippage_rate
    breakeven = avg_entry * (1 + side * cost_buffer)
    tightened = max(stop, breakeven)
    assert tightened > stop
    # a second application cannot loosen the stop
    assert max(tightened, breakeven) == tightened


def test_metrics_expose_arm_c_fields() -> None:
    bars = _bars([100.0] * 400)
    result = ledger.run_plan(
        bars=bars, zone=ZONE, start=bars[0].timestamp, end=bars[-1].timestamp,
        plan=ledger.PLANS["C1"], capital=100.0,
    )
    assert "stop_tightenings" in result["metrics"]
    assert "max_open_risk_pct" in result["metrics"]
    assert result["metrics"]["stop_tightenings"] == 0
    assert all(result["ledger_checks"].values())
