"""Pure, side-effect-free formatting for saved Telegram historical backtests.

No network calls, database access, command dispatch or trading permissions.
Invalid optional fields are rendered as N/A or omitted, never fabricated.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


def _one_line(value: object, limit: int = 180) -> str:
    """Normalize error text before placing it in a chat response."""
    return " ".join(str(value).replace("\x00", "").split())[:limit]


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _money(value: object) -> str:
    number = _finite_number(value)
    return "N/A" if number is None else f"{number:+,.2f} USDT"


def _percent(value: object, *, signed: bool = False) -> str:
    number = _finite_number(value)
    if number is None:
        return "N/A"
    return f"{number:+.4f}%" if signed else f"{number:.4f}%"


def _daily_pnl_lines(period: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    """Build a UTC daily PnL table from a complete 4-hour holdout equity curve."""
    start_text = period.get("test_start_at")
    end_text = period.get("test_end_at_exclusive")
    curve = metrics.get("equity_curve")
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        return []
    if not isinstance(curve, list):
        return []

    try:
        start_at = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        end_exclusive = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
        if start_at.tzinfo is None or end_exclusive.tzinfo is None:
            return []
        start_at = start_at.astimezone(timezone.utc)
        end_exclusive = end_exclusive.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return []

    duration = (end_exclusive - start_at).total_seconds() / 86400
    if (
        start_at.hour != 0
        or start_at.minute != 0
        or start_at.second != 0
        or duration != int(duration)
        or not 1 <= int(duration) <= 30
    ):
        return []

    points: list[tuple[datetime, float]] = []
    for item in curve:
        if not isinstance(item, dict):
            return []
        timestamp = item.get("timestamp")
        equity = _finite_number(item.get("equity"))
        if not isinstance(timestamp, str) or equity is None:
            return []
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return []
        if parsed.tzinfo is None:
            return []
        points.append((parsed.astimezone(timezone.utc), equity))

    points.sort(key=lambda item: item[0])
    timestamps = [point[0] for point in points]
    if not points or len(timestamps) != len(set(timestamps)):
        return []

    rows: list[str] = []
    for day_index in range(int(duration)):
        day_start = start_at + timedelta(days=day_index)
        day_end = day_start + timedelta(days=1)
        day_points = [
            point for point in points if day_start <= point[0] < day_end
        ]
        prior_points = [point for point in points if point[0] < day_start]
        # The engine records six 4-hour equity marks per test day plus the
        # immediately preceding mark. Reject incomplete curves rather than
        # presenting a misleading daily return.
        if (
            len(day_points) != 6
            or not prior_points
            or prior_points[-1][0] != day_start - timedelta(hours=4)
            or day_points[-1][0] != day_start + timedelta(hours=20)
        ):
            return []
        opening_equity = prior_points[-1][1]
        closing_equity = day_points[-1][1]
        daily_pnl = closing_equity - opening_equity
        daily_return = (
            daily_pnl / opening_equity * 100.0 if opening_equity > 0 else None
        )
        daily_return_text = (
            "N/A" if daily_return is None else f"{daily_return:+.4f}%"
        )
        rows.append(
            f"{day_start.date().isoformat()} | {_money(daily_pnl)} | "
            f"{daily_return_text} | {closing_equity:,.2f} USDT"
        )

    if not rows:
        return []
    return [
        "每日收益明细（UTC；由 4 小时权益曲线计算）",
        "日期 | 净盈亏 | 日收益率 | 日终权益",
        *rows,
    ]



def _cost_sensitivity_lines(report: dict[str, Any]) -> list[str]:
    """Render the complete fixed 3x3 cost matrix without implying a new selection."""
    cost_sensitivity = report.get("cost_sensitivity")
    if not isinstance(cost_sensitivity, dict):
        return []
    if (
        cost_sensitivity.get("strategy_reselected_per_scenario") is not False
        or cost_sensitivity.get("test_window_changed_per_scenario") is not False
        or cost_sensitivity.get("stress_test_only") is not True
    ):
        return []

    scenarios = cost_sensitivity.get("scenarios")
    # This protocol promises a fixed 3x3 matrix. Omit partial or malformed data.
    if not isinstance(scenarios, list) or len(scenarios) != 9:
        return []

    expected_pairs = {
        (5.0, 0.0),
        (5.0, 5.0),
        (5.0, 10.0),
        (10.0, 0.0),
        (10.0, 5.0),
        (10.0, 10.0),
        (20.0, 0.0),
        (20.0, 5.0),
        (20.0, 10.0),
    }
    rows: list[tuple[float, float, float, float, float, int]] = []
    seen_pairs: set[tuple[float, float]] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            return []
        fee = _finite_number(scenario.get("fee_bps_per_side"))
        slippage = _finite_number(scenario.get("slippage_bps_per_side"))
        net_return = _finite_number(scenario.get("net_return_pct"))
        net_pnl = _finite_number(scenario.get("net_pnl"))
        drawdown = _finite_number(scenario.get("max_drawdown_pct"))
        trades = scenario.get("number_of_trades")
        if (
            fee is None
            or slippage is None
            or net_return is None
            or net_pnl is None
            or drawdown is None
            or isinstance(trades, bool)
            or not isinstance(trades, int)
            or trades < 0
            or fee < 0
            or slippage < 0
            or drawdown < 0
        ):
            return []
        pair = (fee, slippage)
        if pair not in expected_pairs or pair in seen_pairs:
            return []
        seen_pairs.add(pair)
        rows.append((fee, slippage, net_return, net_pnl, drawdown, trades))

    if seen_pairs != expected_pairs:
        return []
    rows.sort(key=lambda row: (row[0], row[1]))
    rendered_rows = [
        f"{fee:g}/{slippage:g} bps | {_percent(net_return, signed=True)} | "
        f"{_money(net_pnl)} | {_percent(drawdown)} | {trades}"
        for fee, slippage, net_return, net_pnl, drawdown, trades in rows
    ]
    return [
        "交易成本敏感性（手续费/滑点均为单边；策略与测试区间固定）",
        "手续费/滑点 | 净收益率 | 净盈亏 | 最大回撤 | 交易数",
        *rendered_rows,
    ]


def _candidate_selection_lines(selection: dict[str, Any]) -> list[str]:
    """Show pre-test candidate evidence while distinguishing it from holdout results."""
    selected = selection.get("selected_candidate")
    candidates = selection.get("candidates")
    if not isinstance(selected, dict) or not isinstance(candidates, list):
        return []
    if not 1 <= len(candidates) <= 12:
        return []
    selected_id = selected.get("candidate_id")
    if not isinstance(selected_id, str) or not selected_id:
        return []

    rows: list[str] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return []
        candidate_id = candidate.get("candidate_id")
        validation_return = _finite_number(candidate.get("total_return_pct"))
        drawdown = _finite_number(candidate.get("max_drawdown_pct"))
        score = _finite_number(candidate.get("selection_score"))
        trades = candidate.get("number_of_trades")
        eligible = candidate.get("eligible")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.strip()
            or candidate_id in seen_ids
            or validation_return is None
            or drawdown is None
            or score is None
            or drawdown < 0
            or isinstance(trades, bool)
            or not isinstance(trades, int)
            or trades < 0
            or not isinstance(eligible, bool)
        ):
            return []
        seen_ids.add(candidate_id)
        is_selected = candidate_id == selected_id
        if is_selected:
            state = "已选" if eligible else "已选·证据不足"
        else:
            state = "达标" if eligible else "证据不足"
        rows.append(
            f"{_one_line(candidate_id, 60)} | {_percent(validation_return, signed=True)} | "
            f"{_percent(drawdown)} | {trades} | {score:+.4f} | {state}"
        )

    if selected_id not in seen_ids:
        return []
    return [
        "策略候选验证（仅训练期数据；不代表测试期结果）",
        "候选 | 验证收益 | 最大回撤 | 交易数 | 选择分 | 状态",
        *rows,
    ]


def format_backtest_record(record: object) -> str:
    """Render the useful summary from a persisted date-range backtest record."""
    if not isinstance(record, dict):
        return "回测记录格式无法识别，请检查 API。"
    report = record.get("report")
    if not isinstance(report, dict):
        return "回测记录缺少报告内容，请检查 API。"

    period = report.get("period")
    market = report.get("market")
    selection = report.get("strategy_selection")
    metrics = report.get("test_result")
    if not all(isinstance(item, dict) for item in (period, market, selection, metrics)):
        return "回测记录缺少必要字段，请检查 API。"

    selected = selection.get("selected_candidate")
    selected = selected if isinstance(selected, dict) else {}
    parameters = selected.get("parameters", {})
    parameters = parameters if isinstance(parameters, dict) else {}
    candidate_id = _one_line(selected.get("candidate_id", "unknown"), 80)
    strategy_type = _one_line(selected.get("strategy_type", "unknown"), 80)
    strategy_labels = {
        "sma_crossover": "SMA 均线交叉",
        "donchian_breakout": "Donchian 突破",
    }
    parameter_text = _one_line(
        ", ".join(
            f"{key}={value}"
            for key, value in sorted(parameters.items(), key=lambda pair: str(pair[0]))
        ),
        240,
    ) or "无参数记录"

    raw_selection_status = _one_line(selection.get("status", "unknown"), 100)
    if "insufficient" in raw_selection_status.lower():
        selection_status = "验证交易不足（结果仅供研究）"
    elif "sufficient" in raw_selection_status.lower():
        selection_status = "达到最低验证交易数要求"
    else:
        selection_status = raw_selection_status

    record_id = _one_line(record.get("id", "unknown"), 64)
    lines = [
        "Emergenzix · 历史回测完成",
        f"区间（UTC）：{_one_line(period.get('test_start_at', 'N/A'), 40)} 至",
        f"{_one_line(period.get('test_end_at_exclusive', 'N/A'), 40)}（结束时间不包含在内）",
        f"市场：{_one_line(market.get('symbol', 'BTC/USDT'), 50)} · {_one_line(market.get('timeframe', '4h'), 20)}",
        f"策略：{candidate_id}（{strategy_labels.get(strategy_type, strategy_type)}）",
        f"参数：{parameter_text}",
        f"净盈亏：{_money(metrics.get('net_pnl'))}",
        f"净收益率：{_percent(metrics.get('total_return_pct'), signed=True)}",
        f"最大回撤：{_percent(metrics.get('max_drawdown_pct'))}",
        f"已平仓交易：{_one_line(metrics.get('number_of_trades', 'N/A'), 30)}",
        f"胜率：{_percent(metrics.get('win_rate_pct'))}",
        f"策略选择证据：{selection_status}",
    ]
    candidate_lines = _candidate_selection_lines(selection)
    if candidate_lines:
        lines.extend(["", *candidate_lines])
    daily_lines = _daily_pnl_lines(period, metrics)
    if daily_lines:
        lines.extend(["", *daily_lines])
    cost_lines = _cost_sensitivity_lines(report)
    if cost_lines:
        lines.extend(["", *cost_lines])
    lines.extend([
        f"记录 ID：{record_id[:16]}",
        "安全：仅历史 PAPER 模拟，没有创建任何订单；历史结果不代表未来收益。",
    ])
    return "\n".join(lines)
