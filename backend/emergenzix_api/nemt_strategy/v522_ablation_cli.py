"""NEMT v5.2.2: 55-day slow-system ablation with audited execution semantics.

Two frozen arms share every parameter except the Donchian entry window and the
opposite-channel exit window.  Both arms run on the same corrected execution
engine, so the A/B contrast is not confounded by the execution defects that
separately tracked v5.2.3 (entry-bar protective stop) and v5.2.4 (gap-through
stop, exit event ordering).

Execution contract implemented here:

1. A signal is decided on the close of completed bar ``t`` (channel excludes
   ``t`` itself, per v5.2.1) and is filled at the open of ``t + 1``.
2. The protective stop becomes active the moment the position is filled, so it
   is checked inside the entry bar as well (v5.2.3).
3. For a position carried into bar ``t``, events are resolved in this order:
   open gap through the stop (filled at the open, never at the stop price),
   then a pending opposite-channel exit (filled at the open), then an intrabar
   touch of the stop (filled at the stop price) (v5.2.4).
4. No position may be opened on a bar that already carried a position into it.

Research only.  No PAPER, TESTNET, or LIVE order path exists in this module.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from emergenzix_api.historical_data import fetch_binance_btc_4h_archive
from emergenzix_api.schemas import Bar

from .nemt_momentum_v2 import compute_momentum_state

FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002
ATR_BARS = 120
STOP_MULTIPLE = 2.0
RISK_FRACTION_OF_EQUITY = 0.01
MAXIMUM_LEVERAGE = 3.0
PROTOCOL = "nemt-v5.2.2-slow-system-ablation-001"


@dataclass(frozen=True)
class Arm:
    """One frozen research arm."""

    key: str
    label: str
    entry_bars: int
    exit_bars: int


ARMS: dict[str, Arm] = {
    "A": Arm("A", "a_fast_20d_priority", 120, 60),
    "B": Arm("B", "b_slow_55d_independent", 330, 120),
}

# Identifier of the published v5.2 implementation this ablation is based on.
PUBLISHED_V52_COMMIT = "d6962bedb9cf18e927ac9ae768e55eb4a57ea0d9"


def atr(bars: list[Bar], index: int, period: int = ATR_BARS) -> float:
    """Equal-weight mean of true range, matching the published v5.2 estimator."""
    values = []
    for cursor in range(index - period + 1, index + 1):
        previous = bars[cursor - 1].close
        values.append(max(bars[cursor].high - bars[cursor].low,
                          abs(bars[cursor].high - previous),
                          abs(bars[cursor].low - previous)))
    return statistics.mean(values)


def breakout(bars: list[Bar], index: int, lookback: int) -> int:
    """Channel of the ``lookback`` bars strictly before ``index`` (v5.2.1)."""
    history = bars[index - lookback:index]
    close = bars[index].close
    return 1 if close > max(bar.high for bar in history) else (
        -1 if close < min(bar.low for bar in history) else 0
    )


def channel_exit(bars: list[Bar], index: int, exit_bars: int, side: int) -> bool:
    """Opposite-channel break evaluated on the close of bar ``index``."""
    history = bars[index - exit_bars:index]
    close = bars[index].close
    return (close < min(bar.low for bar in history) if side == 1
            else close > max(bar.high for bar in history))


def _psi_direction(state) -> int:
    """Signed momentum only, ignoring the histogram-entropy condition.

    This is the ``M_t`` gate frozen by the v5.2.7 protocol: it keeps the
    direction and magnitude rule of ``psi`` but drops ``S > 0.7``.
    """
    return 1 if state.psi > 0.5 else -1 if state.psi < -0.5 else 0


def _drawdown(equities: list[float]) -> float:
    peak, maximum = equities[0], 0.0
    for equity in equities:
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak if peak else 0.0)
    return maximum


def _direction(state) -> int:
    return 1 if state.phase == "critical_up" else -1 if state.phase == "critical_down" else 0


def run_arm(
    *, bars: list[Bar], arm: Arm, zone: ZoneInfo, start: datetime, end: datetime,
    capital: float = 100.0, fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE, observation_bars: int = 42,
    gate: str = "nemt",
) -> dict:
    """Run one arm over one window and return a fully audited report.

    ``gate="nemt"`` is the frozen v5.2.2 behaviour.  ``gate="none"`` removes the
    NEMT permission so the same engine can serve as the Donchian-only baseline
    required by the v5.2.6 incremental-information protocol.
    """
    if gate not in {"nemt", "none", "psi"}:
        raise ValueError("gate must be 'nemt', 'psi' or 'none'")
    closes = [bar.close for bar in bars]
    warmup = max(arm.entry_bars, ATR_BARS) + 1
    cash, position, pending = capital, None, None
    trades: list[dict] = []
    curve: list[dict] = []
    counters = {
        "nemt_directional_events": 0,
        "donchian_same_direction_breakouts": 0,
        "actual_entries": 0,
        "observable_entries": 0,
        "censored_entries": 0,
        "early_protective_stops": 0,
        "early_2n_stops": 0,
        "early_gap_stops": 0,
    }
    last_window_index = None

    def close_position(index: int, raw_price: float, reason: str) -> None:
        nonlocal cash, position
        side = position["direction"]
        exit_price = raw_price * (1 - side * slippage_rate)
        gross = position["quantity"] * (exit_price - position["entry_price"]) * side
        exit_fee = position["quantity"] * exit_price * fee_rate
        net = gross - position["entry_fee"] - exit_fee
        cash += net
        trades.append({
            **position, "exit_index": index,
            "exit_at": bars[index].timestamp.isoformat(), "exit_price": exit_price,
            "exit_reason": reason,
            "holding_bars": index - position["entry_index"],
            "holding_days": (index - position["entry_index"]) / 6.0,
            "net_pnl": net, "equity_after": cash,
        })
        position = None

    for index in range(warmup, len(bars)):
        bar = bars[index]
        if not (start <= bar.timestamp.astimezone(zone) < end):
            continue
        last_window_index = index
        decision_index = index - 1
        decision_local = bars[decision_index].timestamp.astimezone(zone)
        in_decision_window = start <= decision_local < end

        closed_this_bar = False

        # 1) Resolve exits for a position carried into this bar.
        if position is not None:
            side = position["direction"]
            stop = position["stop"]
            gap = bar.open <= stop if side == 1 else bar.open >= stop
            pending_channel = (
                in_decision_window
                and channel_exit(bars, decision_index, position["exit_bars"], side)
            )
            touched = bar.low <= stop if side == 1 else bar.high >= stop
            if gap:
                close_position(index, bar.open, "gap_stop")
                closed_this_bar = True
            elif pending_channel:
                close_position(index, bar.open, "opposite_channel")
                closed_this_bar = True
            elif touched:
                close_position(index, stop, "2N_stop")
                closed_this_bar = True

        # 2) Fill the signal decided on the previous close.
        if (position is None and not closed_this_bar and pending is not None
                and pending["decision_index"] == decision_index):
            side = pending["direction"]
            entry = bar.open * (1 + side * slippage_rate)
            stop_distance = max(STOP_MULTIPLE * pending["n_value"], entry * 0.01)
            risk_fraction = stop_distance / entry
            notional = min(cash * MAXIMUM_LEVERAGE,
                           cash * RISK_FRACTION_OF_EQUITY / risk_fraction)
            quantity = notional / entry
            position = {
                "arm": arm.key, "direction": side,
                "side": "LONG" if side == 1 else "SHORT",
                "entry_index": index, "entry_at": bar.timestamp.isoformat(),
                "entry_price": entry, "quantity": quantity, "notional": notional,
                "leverage": notional / cash, "entry_fee": notional * fee_rate,
                "n_value": pending["n_value"], "stop": entry - side * stop_distance,
                "exit_bars": arm.exit_bars, "entry_bars": arm.entry_bars,
                "initial_risk_usdt": cash * RISK_FRACTION_OF_EQUITY,
                "nemt_psi": pending["psi"], "nemt_entropy": pending["entropy"],
            }
            counters["actual_entries"] += 1
            # v5.2.3: the protective stop is live from the fill moment, so it must
            # be checked inside the very bar that opened the position.
            side = position["direction"]
            entry_stop = position["stop"]
            if (bar.low <= entry_stop if side == 1 else bar.high >= entry_stop):
                close_position(index, entry_stop, "2N_stop")
                closed_this_bar = True
        pending = None

        # 3) Decide on this close for the next open.
        state = compute_momentum_state(closes[:index + 1])
        nemt_direction = _direction(state)
        break_direction = breakout(bars, index, arm.entry_bars)
        if nemt_direction:
            counters["nemt_directional_events"] += 1
        if gate == "nemt":
            entry_side = nemt_direction if break_direction == nemt_direction else 0
            if nemt_direction and break_direction == nemt_direction:
                counters["donchian_same_direction_breakouts"] += 1
        elif gate == "psi":
            psi_side = _psi_direction(state)
            if psi_side and break_direction == psi_side:
                counters["donchian_same_direction_breakouts"] += 1
                entry_side = psi_side
            else:
                entry_side = 0
        else:
            if break_direction:
                counters["donchian_same_direction_breakouts"] += 1
            entry_side = break_direction
        if entry_side and position is None:
            pending = {"decision_index": index, "direction": entry_side,
                       "n_value": atr(bars, index), "psi": state.psi,
                       "entropy": state.entropy}

        unrealized = 0.0
        if position is not None:
            unrealized = (position["quantity"]
                          * (bar.close - position["entry_price"]) * position["direction"]
                          - position["entry_fee"])
        curve.append({"time": bar.timestamp.isoformat(), "equity": cash + unrealized})

    if position is not None:
        last = bars[last_window_index]
        close_position(last_window_index, last.close, "end_of_period")
        if curve:
            curve[-1]["equity"] = cash

    # Censoring: an entry is observable only if the full observation window of
    # completed bars existed inside the evaluation window.
    for trade in trades:
        # Observable when the full 42-bar path existed, or when the trade closed
        # before 42 bars (the pre-exit path was therefore fully observed).
        observable = (
            trade["entry_index"] + observation_bars <= last_window_index
            or trade["exit_reason"] != "end_of_period"
        )
        trade["observable"] = observable
        trade["censored"] = not observable
        if observable:
            counters["observable_entries"] += 1
            if trade["holding_bars"] <= observation_bars and trade["exit_reason"] in {
                "2N_stop", "gap_stop"
            }:
                counters["early_protective_stops"] += 1
                if trade["exit_reason"] == "2N_stop":
                    counters["early_2n_stops"] += 1
                else:
                    counters["early_gap_stops"] += 1
        else:
            counters["censored_entries"] += 1

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    equities = [capital] + [point["equity"] for point in curve]
    r_multiples = [t["net_pnl"] / t["initial_risk_usdt"] for t in trades
                   if t["initial_risk_usdt"] > 0]
    observable = counters["observable_entries"]
    metrics = {
        "initial_equity": capital, "final_equity": cash,
        "total_return_pct": (cash / capital - 1) * 100,
        "max_drawdown_pct": _drawdown(equities) * 100 if equities else 0.0,
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "payoff_ratio": ((gross_win / len(wins)) / (gross_loss / len(losses))
                         if wins and losses else None),
        "average_holding_days": (statistics.mean(t["holding_days"] for t in trades)
                                 if trades else 0.0),
        "average_r_multiple": statistics.mean(r_multiples) if r_multiples else 0.0,
        "longs": sum(t["side"] == "LONG" for t in trades),
        "shorts": sum(t["side"] == "SHORT" for t in trades),
        "early_protective_stop_ratio": (
            counters["early_protective_stops"] / observable if observable else None
        ),
        "early_2n_stop_ratio": (
            counters["early_2n_stops"] / observable if observable else None
        ),
    }
    return {"arm": arm.key, "arm_label": arm.label,
            "entry_bars": arm.entry_bars, "exit_bars": arm.exit_bars,
            "observation_bars": observation_bars, "metrics": metrics,
            "counters": counters, "trades": trades, "equity_curve": curve}


def cost_stress(bars: list[Bar], arm: Arm, zone: ZoneInfo, start: datetime,
                end: datetime, capital: float) -> list[dict]:
    """Nine locked fee/slippage combinations, evaluated on the same window."""
    grid = []
    for fee_bps in (5.0, 10.0, 15.0):
        for slippage_bps in (2.0, 5.0, 10.0):
            report = run_arm(
                bars=bars, arm=arm, zone=zone, start=start, end=end, capital=capital,
                fee_rate=fee_bps / 10_000.0, slippage_rate=slippage_bps / 10_000.0,
            )
            grid.append({
                "fee_bps_per_side": fee_bps, "slippage_bps_per_side": slippage_bps,
                "total_return_pct": report["metrics"]["total_return_pct"],
                "trades": report["metrics"]["trades"],
                "early_protective_stop_ratio": report["metrics"]["early_protective_stop_ratio"],
            })
    return grid


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one NEMT v5.2.2 ablation arm")
    parser.add_argument("--arm", choices=sorted(ARMS), required=True)
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-01-01")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--observation-bars", type=int, default=42)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=zone)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=zone)
    arm = ARMS[args.arm]
    lookback_days = arm.entry_bars * 4 // 24 + 30
    payloads = fetch_binance_btc_4h_archive(
        start_at=start.astimezone(timezone.utc) - timedelta(days=lookback_days),
        end_at=end.astimezone(timezone.utc) + timedelta(hours=4),
    )
    bars = [Bar.model_validate(item) for item in payloads]
    report = run_arm(bars=bars, arm=arm, zone=zone, start=start, end=end,
                     capital=args.capital, observation_bars=args.observation_bars)
    report.update({
        "protocol": PROTOCOL,
        "based_on_published_commit": PUBLISHED_V52_COMMIT,
        "start_local": start.isoformat(), "end_local_exclusive": end.isoformat(),
        "execution_contract": {
            "signal": "close of completed bar t, channel excludes t",
            "fill": "open of t+1",
            "entry_bar_stop": "active from fill, checked in the entry bar",
            "event_order": ["gap_stop@open", "opposite_channel@open", "2N_stop@stop_price"],
            "reentry_same_bar": False,
        },
        "assumptions": {
            "fee_rate_each_side": FEE_RATE, "slippage_rate_each_side": SLIPPAGE_RATE,
            "atr_bars": ATR_BARS, "stop_multiple": STOP_MULTIPLE,
            "risk_fraction_of_equity": RISK_FRACTION_OF_EQUITY,
            "maximum_leverage": MAXIMUM_LEVERAGE, "pyramiding": False,
            "funding_modeled": False, "liquidation_modeled": False,
        },
        "limitations": [
            "Single BTC market; the original Turtles diversified across futures.",
            "Funding, liquidation tiers, latency and order-book depth are not modeled.",
            "Historical simulation only; no orders were created.",
            "A and B differ in both entry and exit window, so any difference cannot "
            "be attributed to the slow entry alone.",
        ],
    })
    if args.stress:
        report["cost_stress"] = cost_stress(bars, arm, zone, start, end, args.capital)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"arm": arm.label, "output": str(args.output),
                      "metrics": report["metrics"], "counters": report["counters"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
