"""Causal outer walk-forward protocol for comparable research arms.

This module is deliberately execution-free: callers provide pure research runners.
The scheduler owns the calendar split and prevents a runner from seeing bars after
the selection or test boundary.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .parameter_stability import ParameterEvaluation, StabilitySelection


@dataclass(frozen=True)
class WalkForwardProtocol:
    training_months: int = 18
    selection_months: int = 3
    test_months: int = 3
    step_months: int = 3
    initial_capital: float = 100.0
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    risk_fraction: float = 0.01
    maximum_leverage: float = 3.0


@dataclass(frozen=True)
class Window:
    index: int
    train_start: datetime
    selection_start: datetime
    test_start: datetime
    test_end: datetime


Runner = Callable[[Sequence[Any], datetime, datetime, Mapping[str, Any], WalkForwardProtocol], Mapping[str, Any]]
Selector = Callable[[Sequence[ParameterEvaluation]], StabilitySelection]


def add_months(value: datetime, months: int) -> datetime:
    """Add calendar months; protocol anchors must be first-of-month."""
    if value.day != 1 or any((value.hour, value.minute, value.second, value.microsecond)):
        raise ValueError("walk-forward anchors must be midnight on the first day of a month")
    absolute = value.year * 12 + value.month - 1 + months
    return value.replace(year=absolute // 12, month=absolute % 12 + 1)


def build_windows(anchor: datetime, data_end: datetime, protocol: WalkForwardProtocol) -> list[Window]:
    if min(protocol.training_months, protocol.selection_months, protocol.test_months, protocol.step_months) <= 0:
        raise ValueError("all window lengths must be positive")
    windows: list[Window] = []
    train_start = anchor
    while True:
        selection_start = add_months(train_start, protocol.training_months)
        test_start = add_months(selection_start, protocol.selection_months)
        test_end = add_months(test_start, protocol.test_months)
        if test_end > data_end:
            break
        windows.append(Window(len(windows), train_start, selection_start, test_start, test_end))
        train_start = add_months(train_start, protocol.step_months)
    return windows


def _timestamp(bar: Any) -> datetime:
    value = bar.timestamp if hasattr(bar, "timestamp") else bar["timestamp"]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("every bar timestamp must be timezone-aware")
    return value


def _prefix(bars: Sequence[Any], end: datetime) -> list[Any]:
    return [bar for bar in bars if _timestamp(bar) < end]


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _validate_result(result: Mapping[str, Any]) -> None:
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("runner result must contain metrics")
    for key in ("total_return_pct", "max_drawdown_pct"):
        if key not in metrics:
            raise ValueError(f"runner metrics missing {key}")
    if not isinstance(result.get("trades", []), list):
        raise ValueError("runner trades must be a list")


def _without_largest_winners(trades: Sequence[Mapping[str, Any]], capital: float) -> dict[str, float | int]:
    pnls = [float(trade["net_pnl"]) for trade in trades]
    positive = sorted((value for value in pnls if value > 0), reverse=True)
    removed = positive[:1]
    remaining = list(pnls)
    if removed:
        remaining.remove(removed[0])
    return {
        "removed_winners": len(removed),
        "remaining_net_pnl": sum(remaining),
        "remaining_return_pct": sum(remaining) / capital * 100.0,
        "remaining_expectancy": statistics.mean(remaining) if remaining else 0.0,
    }


def run_walk_forward(
    *, bars: Sequence[Any], anchor: datetime, data_end: datetime,
    runners: Mapping[str, Runner], candidates: Mapping[str, Sequence[Mapping[str, Any]]],
    selectors: Mapping[str, Selector] | None = None,
    protocol: WalkForwardProtocol = WalkForwardProtocol(),
) -> dict[str, Any]:
    """Select on each selection window, freeze parameters, then evaluate unseen data."""
    arms = tuple(runners)
    if not arms:
        raise ValueError("at least one research arm is required")
    if len(arms) != len(set(arms)) or set(candidates) != set(arms):
        raise ValueError("runner and candidate arm identifiers must match exactly")
    if selectors is not None and not set(selectors).issubset(arms):
        raise ValueError("selector arm identifiers must belong to the experiment")
    ordered_bars = sorted(bars, key=_timestamp)
    windows = build_windows(anchor, data_end, protocol)
    records: list[dict[str, Any]] = []
    for window in windows:
        selection_visible = _prefix(ordered_bars, window.test_start)
        test_visible = _prefix(ordered_bars, window.test_end)
        for arm in arms:
            options = list(candidates[arm])
            if not options:
                raise ValueError(f"no parameter candidates for {arm}")
            scored: list[tuple[float, str, Mapping[str, Any]]] = []
            for params in options:
                result = runners[arm](selection_visible, window.selection_start, window.test_start, params, protocol)
                _validate_result(result)
                score = float(result["metrics"]["total_return_pct"])
                scored.append((score, _canonical_hash(params), params))
            selection_diagnostics: Mapping[str, Any] = {"selection_status": "maximum_score"}
            if selectors is not None and arm in selectors:
                stability = selectors[arm]([
                    ParameterEvaluation(parameters=params, score=score)
                    for score, _, params in scored
                ])
                selected = stability.parameters
                selection_diagnostics = stability.diagnostics
            else:
                _, _, selected = max(scored, key=lambda item: (item[0], item[1]))
            result = dict(runners[arm](test_visible, window.test_start, window.test_end, selected, protocol))
            _validate_result(result)
            records.append({
                "window_index": window.index,
                "arm": arm,
                "train_start": window.train_start.isoformat(),
                "selection_start": window.selection_start.isoformat(),
                "test_start": window.test_start.isoformat(),
                "test_end_exclusive": window.test_end.isoformat(),
                "selected_parameters": dict(selected),
                "selection_diagnostics": dict(selection_diagnostics),
                "metrics": dict(result["metrics"]),
                "trades": result.get("trades", []),
                "without_largest_winner": _without_largest_winners(
                    result.get("trades", []), protocol.initial_capital
                ),
            })
    summaries: dict[str, Any] = {}
    for arm in arms:
        arm_records = [record for record in records if record["arm"] == arm]
        parameters = [record["selected_parameters"] for record in arm_records]
        summaries[arm] = {
            "windows": len(arm_records),
            "profitable_windows": sum(record["metrics"]["total_return_pct"] > 0 for record in arm_records),
            "median_return_pct": statistics.median(
                [float(record["metrics"]["total_return_pct"]) for record in arm_records]
            ) if arm_records else 0.0,
            "worst_max_drawdown_pct": max(
                [float(record["metrics"]["max_drawdown_pct"]) for record in arm_records], default=0.0
            ),
            "parameter_changes": sum(left != right for left, right in zip(parameters, parameters[1:])),
        }
    report: dict[str, Any] = {
        "protocol": asdict(protocol), "windows": records, "summaries": summaries,
        "execution_mode": "BACKTEST_ONLY", "live_enabled": False,
    }
    report["report_fingerprint"] = _canonical_hash(report)
    return report
