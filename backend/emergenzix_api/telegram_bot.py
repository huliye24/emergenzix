"""Allowlisted Telegram command interface for historical PAPER research only."""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


class TelegramBotError(RuntimeError):
    """A sanitized Telegram transport or protocol failure."""


class ApiRequestError(RuntimeError):
    """A sanitized error while calling the private Emergenzix API."""


def _one_line(value: object, limit: int = 180) -> str:
    """Normalize error text before placing it in a chat response."""
    return " ".join(str(value).replace("\x00", "").split())[:limit]


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_record_id(value: object) -> bool:
    """Accept only canonical SHA-256 record IDs, never path-like user input."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
    candidate_id = str(selected.get("candidate_id", "unknown"))
    strategy_type = str(selected.get("strategy_type", "unknown"))
    strategy_labels = {
        "sma_crossover": "SMA 均线交叉",
        "donchian_breakout": "Donchian 突破",
    }
    parameter_text = ", ".join(
        f"{key}={value}" for key, value in sorted(parameters.items())
    ) or "无参数记录"

    raw_selection_status = str(selection.get("status", "unknown"))
    if "insufficient" in raw_selection_status.lower():
        selection_status = "验证交易不足（结果仅供研究）"
    elif "sufficient" in raw_selection_status.lower():
        selection_status = "达到最低验证交易数要求"
    else:
        selection_status = raw_selection_status

    record_id = str(record.get("id", "unknown"))
    lines = [
        "Emergenzix · 历史回测完成",
        f"区间（UTC）：{period.get('test_start_at', 'N/A')} 至",
        f"{period.get('test_end_at_exclusive', 'N/A')}（结束时间不包含在内）",
        f"市场：{market.get('symbol', 'BTC/USDT')} · {market.get('timeframe', '4h')}",
        f"策略：{candidate_id}（{strategy_labels.get(strategy_type, strategy_type)}）",
        f"参数：{parameter_text}",
        f"净盈亏：{_money(metrics.get('net_pnl'))}",
        f"净收益率：{_percent(metrics.get('total_return_pct'), signed=True)}",
        f"最大回撤：{_percent(metrics.get('max_drawdown_pct'))}",
        f"已平仓交易：{metrics.get('number_of_trades', 'N/A')}",
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


class TelegramCommandBot:
    """Poll Telegram updates and expose a small, allowlisted research command set."""

    def __init__(
        self,
        *,
        bot_token: str,
        allowed_chat_id: str,
        api_base_url: str = "http://127.0.0.1:8000",
        state_path: str | Path = "data/telegram-bot-state.json",
        poll_timeout_seconds: int = 25,
        backtest_cooldown_seconds: int = 60,
        requester: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token is required")
        if not allowed_chat_id.strip():
            raise ValueError("an explicit Telegram allowed chat ID is required")
        parsed_url = urllib.parse.urlparse(api_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("API base URL must be an absolute HTTP(S) URL")
        if not 1 <= poll_timeout_seconds <= 50:
            raise ValueError("Telegram poll timeout must be between 1 and 50 seconds")
        if backtest_cooldown_seconds < 0:
            raise ValueError("backtest cooldown must not be negative")

        self.bot_token = bot_token.strip()
        self.allowed_chat_id = allowed_chat_id.strip()
        self.api_base_url = api_base_url.rstrip("/")
        self.state_path = Path(state_path).expanduser()
        self.poll_timeout_seconds = poll_timeout_seconds
        self.backtest_cooldown_seconds = backtest_cooldown_seconds
        self.requester = requester
        self.clock = clock
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"last_update_id": None, "last_backtest_at": None, "poll_initialized": False}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Telegram bot state file cannot be read") from exc
        if not isinstance(payload, dict):
            raise ValueError("Telegram bot state file has an invalid structure")

        update_id = payload.get("last_update_id")
        if update_id is not None and (
            isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0
        ):
            raise ValueError("Telegram bot state file has an invalid update ID")
        last_backtest_at = payload.get("last_backtest_at")
        if last_backtest_at is not None and (
            isinstance(last_backtest_at, bool)
            or not isinstance(last_backtest_at, (int, float))
            or not math.isfinite(float(last_backtest_at))
            or float(last_backtest_at) < 0
        ):
            raise ValueError("Telegram bot state file has an invalid cooldown timestamp")
        poll_initialized = payload.get("poll_initialized", False)
        if not isinstance(poll_initialized, bool):
            raise ValueError("Telegram bot state file has an invalid initialization flag")
        return {
            "last_update_id": update_id,
            "last_backtest_at": last_backtest_at,
            "poll_initialized": poll_initialized,
        }

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _telegram_call(
        self, method: str, payload: dict[str, Any], *, timeout: float
    ) -> Any:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        if method == "getUpdates":
            query = {
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for key, value in payload.items()
            }
            request = urllib.request.Request(
                f"{endpoint}?{urllib.parse.urlencode(query)}",
                headers={"Accept": "application/json"},
                method="GET",
            )
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

        try:
            response = self.requester(request, timeout=timeout)
            result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError, UnicodeDecodeError, ValueError) as exc:
            # Never include exception text: Telegram transport errors may contain the token URL.
            raise TelegramBotError(f"Telegram {method} request failed") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramBotError(f"Telegram {method} request failed")
        return result.get("result")

    def _api_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.api_base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = self.requester(request, timeout=180 if method == "POST" else 10)
            return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                error_payload = {}
            detail = error_payload.get("detail") if isinstance(error_payload, dict) else None
            if not isinstance(detail, str) or not detail.strip():
                detail = "API request rejected"
            detail = _one_line(detail, 220).replace(self.bot_token, "[redacted]")
            raise ApiRequestError(detail or "API request rejected") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ApiRequestError("API 暂时不可用，请稍后重试。") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiRequestError("API 返回了无法识别的响应。") from exc

    def _send_message(self, chat_id: str, text: str) -> None:
        safe_text = text if len(text) <= 3900 else text[:3890] + "\n…（内容已截断）"
        self._telegram_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": safe_text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "Emergenzix · Telegram 命令\n"
            "/status — 查看 API、PAPER 模式与通知队列\n"
            "/latest — 查看最近一次日期区间回测\n"
            "/history — 查看最近 5 条已保存回测及完整记录 ID\n"
            "/report RECORD_ID — 按完整记录 ID 查看历史报告\n"
            "/backtest YYYY-MM-DD [days] — 回测历史区间，days 默认为 7，范围 1–30 天\n"
            "/help — 查看此说明\n\n"
            "回测固定使用 90 天训练窗口、10 bps 单边手续费和 5 bps 单边滑点。"
            "仅运行历史 PAPER 模拟，不会创建订单。"
        )

    def _status_text(self) -> str:
        try:
            health = self._api_request("GET", "/health")
        except ApiRequestError:
            return "无法读取 Emergenzix API 状态。请检查 API 服务；当前未执行任何交易操作。"

        if not isinstance(health, dict):
            return "API 状态响应格式异常；当前未执行任何交易操作。"
        try:
            ready = self._api_request("GET", "/ready")
            readiness = "ready" if isinstance(ready, dict) and ready.get("status") == "ready" else "not ready"
        except ApiRequestError:
            readiness = "not ready / unavailable"

        try:
            notifications = self._api_request("GET", "/api/v1/notifications/status")
            metrics = notifications.get("metrics", {}) if isinstance(notifications, dict) else {}
            if not isinstance(metrics, dict):
                metrics = {}
            queue_status = (
                f"{notifications.get('status', 'unknown')} "
                f"(pending {metrics.get('pending', 'N/A')}, dead {metrics.get('dead', 'N/A')})"
                if isinstance(notifications, dict)
                else "unavailable"
            )
        except ApiRequestError:
            queue_status = "unavailable"

        live_state = (
            "disabled"
            if health.get("live_trading_enabled") is False
            else "not confirmed; commands remain research-only"
        )
        return "\n".join(
            [
                "Emergenzix · 系统状态",
                f"API：{health.get('status', 'unknown')}",
                f"Readiness：{readiness}",
                f"模式：{health.get('mode', 'unknown')}",
                f"LIVE 实盘交易：{live_state}",
                f"通知队列：{queue_status}",
            ]
        )

    def _latest_text(self) -> str:
        try:
            records = self._api_request(
                "GET", "/api/v1/research/date-range-backtests?limit=1"
            )
        except ApiRequestError as exc:
            return f"查询最近回测失败：{_one_line(exc)}"
        if not isinstance(records, list):
            return "回测列表响应格式异常，请检查 API。"
        if not records:
            return "尚无已保存的日期区间回测。发送 /help 查看如何启动一次历史回测。"
        return format_backtest_record(records[0])

    def _history_text(self) -> str:
        """List saved observations without running a strategy or placing orders."""
        try:
            records = self._api_request(
                "GET", "/api/v1/research/date-range-backtests?limit=5"
            )
        except ApiRequestError as exc:
            return f"查询历史回测失败：{_one_line(exc)}"
        if not isinstance(records, list) or len(records) > 5:
            return "回测历史响应格式异常，请检查 API。"
        if not records:
            return "尚无已保存的日期区间回测。发送 /help 查看回测命令。"

        lines = ["Emergenzix · 最近 5 条历史回测（新到旧）"]
        for record in records:
            if not isinstance(record, dict) or not _valid_record_id(record.get("id")):
                return "回测历史响应格式异常，请检查 API。"
            report = record.get("report")
            if not isinstance(report, dict):
                return "回测历史响应格式异常，请检查 API。"
            period = report.get("period")
            selection = report.get("strategy_selection")
            metrics = report.get("test_result")
            if not all(isinstance(value, dict) for value in (period, selection, metrics)):
                return "回测历史响应格式异常，请检查 API。"
            selected = selection.get("selected_candidate")
            start_at = period.get("test_start_at")
            candidate_id = selected.get("candidate_id") if isinstance(selected, dict) else None
            net_return = _finite_number(metrics.get("total_return_pct"))
            if (
                not isinstance(start_at, str)
                or len(start_at) < 10
                or not isinstance(candidate_id, str)
                or not candidate_id.strip()
                or net_return is None
            ):
                return "回测历史响应格式异常，请检查 API。"
            lines.append(
                f"{_one_line(start_at[:10])} | {_one_line(candidate_id, 48)} | "
                f"{_percent(net_return, signed=True)}"
            )
            lines.append(f"/report {record['id']}")

        lines.append("以上为独立历史研究记录，不代表未来收益；没有创建任何订单。")
        return "\\n".join(lines)

    def _report_text(self, arguments: list[str]) -> str:
        usage = "用法：/report RECORD_ID（使用 /history 获取完整的 64 位记录 ID）"
        if len(arguments) != 1 or not _valid_record_id(arguments[0]):
            return usage
        try:
            record = self._api_request(
                "GET", f"/api/v1/research/date-range-backtests/{arguments[0]}"
            )
        except ApiRequestError as exc:
            return f"查询历史报告失败：{_one_line(exc)}"
        return format_backtest_record(record)

    def _backtest_text(self, arguments: list[str], *, chat_id: str | None = None) -> str:
        usage = "用法：/backtest YYYY-MM-DD [days]（days 为 1–30，默认 7）"
        if len(arguments) not in {1, 2}:
            return usage
        try:
            start_date = date.fromisoformat(arguments[0])
            if start_date.isoformat() != arguments[0]:
                return usage
            duration_days = int(arguments[1]) if len(arguments) == 2 else 7
        except (TypeError, ValueError):
            return usage
        if not 1 <= duration_days <= 30:
            return usage

        try:
            end_at = datetime.combine(
                start_date, datetime_time.min, tzinfo=timezone.utc
            ) + timedelta(days=duration_days)
        except OverflowError:
            return usage
        if end_at > datetime.now(timezone.utc):
            return "回测区间必须全部位于过去。请检查日期和天数后重试。"

        now = self.clock()
        previous_attempt = self.state.get("last_backtest_at")
        if previous_attempt is not None:
            elapsed = now - float(previous_attempt)
            if elapsed < self.backtest_cooldown_seconds:
                remaining = max(1, math.ceil(self.backtest_cooldown_seconds - elapsed))
                return f"回测冷却中，请在 {remaining} 秒后再提交。"

        # Persist the cooldown before making a potentially slow historical-data request.
        self.state["last_backtest_at"] = now
        self._save_state()
        payload = {
            "start_date": start_date.isoformat(),
            "duration_days": duration_days,
            "training_days": 90,
            "initial_cash": 10_000.0,
            "fee_bps": 10.0,
            "slippage_bps": 5.0,
        }
        if chat_id is not None:
            try:
                self._send_message(
                    chat_id,
                    f"已接受回测请求：{start_date.isoformat()} 起，{duration_days} 天（UTC）。"
                    "正在获取历史数据并筛选策略；仅 PAPER 研究，不会创建订单。",
                )
            except TelegramBotError as exc:
                # A transient progress-notification failure must not expose secrets or crash the poller.
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_backtest_progress_notice_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
        try:
            record = self._api_request(
                "POST", "/api/v1/research/date-range-backtests", payload
            )
        except ApiRequestError as exc:
            return f"历史回测失败：{_one_line(exc)}\n没有创建任何订单。"
        return format_backtest_record(record)

    def handle_update(self, update: object) -> None:
        if not isinstance(update, dict):
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        # Group chats are rejected even if a group ID is accidentally configured.
        if chat.get("type") != "private":
            return
        chat_id = str(chat.get("id", ""))
        if not chat_id or chat_id != self.allowed_chat_id:
            # Unauthorized senders are intentionally ignored without a response.
            return

        text = message.get("text")
        if not isinstance(text, str):
            return
        parts = text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return
        command = parts[0].split("@", 1)[0].lower()
        arguments = parts[1:]

        if command in {"/help", "/start"}:
            reply = self._help_text()
        elif command == "/status":
            reply = self._status_text()
        elif command == "/latest":
            reply = self._latest_text()
        elif command == "/history":
            reply = self._history_text() if not arguments else "用法：/history（不需要参数）"
        elif command == "/report":
            reply = self._report_text(arguments)
        elif command == "/backtest":
            reply = self._backtest_text(arguments, chat_id=chat_id)
        else:
            reply = "未知命令。发送 /help 查看可用命令。"
        self._send_message(chat_id, reply)

    def poll_once(self) -> None:
        if not self.state.get("poll_initialized", False):
            # Drop updates queued before first startup so an old command cannot trigger work.
            pending = self._telegram_call(
                "getUpdates",
                {"offset": -1, "timeout": 0, "allowed_updates": ["message"]},
                timeout=10,
            )
            if not isinstance(pending, list):
                raise TelegramBotError("Telegram getUpdates returned an invalid response")
            pending_ids = [
                item["update_id"]
                for item in pending
                if isinstance(item, dict)
                and isinstance(item.get("update_id"), int)
                and not isinstance(item.get("update_id"), bool)
                and item["update_id"] >= 0
            ]
            if pending_ids:
                self.state["last_update_id"] = max(pending_ids)
            self.state["poll_initialized"] = True
            self._save_state()
            return

        last_update_id = self.state.get("last_update_id")
        payload: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if last_update_id is not None:
            payload["offset"] = int(last_update_id) + 1
        updates = self._telegram_call(
            "getUpdates",
            payload,
            timeout=self.poll_timeout_seconds + 10,
        )
        if not isinstance(updates, list):
            raise TelegramBotError("Telegram getUpdates returned an invalid response")

        for update in updates:
            if not isinstance(update, dict):
                continue
            try:
                self.handle_update(update)
            except Exception as exc:
                # Log only the exception class; URLs and response bodies may contain secrets.
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_command_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            finally:
                update_id = update.get("update_id")
                if isinstance(update_id, int) and not isinstance(update_id, bool) and update_id >= 0:
                    current = self.state.get("last_update_id")
                    self.state["last_update_id"] = (
                        update_id if current is None else max(int(current), update_id)
                    )
                    self._save_state()

    def run_forever(self) -> None:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_started",
                    "mode": "PAPER",
                    "live_trading_enabled": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_poll_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                time.sleep(5)


def main() -> int:
    token = os.getenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "").strip()
    allowed_chat_id = os.getenv("EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not allowed_chat_id:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_not_started",
                    "reason": "missing_required_configuration",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    try:
        bot = TelegramCommandBot(
            bot_token=token,
            allowed_chat_id=allowed_chat_id,
            api_base_url=os.getenv(
                "EMERGENZIX_TELEGRAM_API_BASE_URL", "http://127.0.0.1:8000"
            ),
            state_path=os.getenv(
                "EMERGENZIX_TELEGRAM_BOT_STATE_PATH",
                "data/telegram-bot-state.json",
            ),
            poll_timeout_seconds=int(
                os.getenv("EMERGENZIX_TELEGRAM_POLL_TIMEOUT_SECONDS", "25")
            ),
            backtest_cooldown_seconds=int(
                os.getenv("EMERGENZIX_TELEGRAM_BACKTEST_COOLDOWN_SECONDS", "60")
            ),
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_not_started",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    try:
        bot.run_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
