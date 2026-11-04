"""Outbound notification adapters. Secrets are read by callers and never logged."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .watch_agent import WatchEvent


SCENARIO_LABELS = {
    "continuation_down": "延续下跌",
    "false_breakdown": "假跌破",
    "range": "震荡区间",
    "reversal_up": "向上反转",
}

EVENT_LABELS = {
    "initial_snapshot": "初始行情快照",
    "scenario_changed": "场景发生切换",
    "trade_trigger": "PAPER 策略触发",
    "risk_alert": "风控警报",
    "risk_recovered": "行情数据恢复",
    "operational_alert": "运维告警",
    "operational_recovery": "运维恢复",
    "watcher_startup": "监控服务启动",
    "paper_execution_alert": "PAPER执行风控告警",
    "ai_market_analysis": "AI 市场分析",
    "shadow_signal": "PAPER 策略信号",
    "shadow_fill": "PAPER 模拟成交",
    "shadow_data_failure": "PAPER 数据失败",
    "ai_research_report": "六小时策略判断报告",
}

SEVERITY_LABELS = {"info": "信息", "warning": "警告", "error": "错误"}


def _translate_instruction(value: object) -> str:
    """Translate known scenario instructions while preserving calculated prices."""
    text = str(value)
    patterns = (
        (r"two completed closes below (.+)", r"连续两根已收盘K线跌破 \1"),
        (r"completed close back above (.+)", r"已收盘K线重新站上 \1"),
        (r"support sweep followed by close above (.+)", r"下探支撑后，已收盘K线重新站上 \1"),
        (r"completed close below (.+)", r"已收盘K线跌破 \1"),
        (r"price remains between (.+) and (.+)", r"价格继续运行在 \1 至 \2 之间"),
        (r"two completed closes outside the range buffer", r"连续两根已收盘K线有效离开震荡区间"),
        (r"two completed closes above (.+)", r"连续两根已收盘K线突破 \1"),
        (r"completed close back below (.+)", r"已收盘K线重新跌回 \1 下方"),
    )
    for pattern, replacement in patterns:
        if re.fullmatch(pattern, text):
            translated = re.sub(pattern, replacement, text)
            return translated.replace("support", "支撑位").replace("resistance", "压力位")
    return text


class NotificationError(RuntimeError):
    """Raised when a notification cannot be delivered without exposing credentials."""


def format_watch_event(event: WatchEvent) -> str:
    """Render a compact Chinese alert from a structured watch event."""
    details = event.details
    levels = details.get("key_levels", {}) if isinstance(details, dict) else {}
    scenario = details.get("scenario") if isinstance(details, dict) else None
    scenario_label = SCENARIO_LABELS.get(str(scenario), str(scenario)) if scenario else None
    event_label = EVENT_LABELS.get(event.event_type, event.event_type)
    severity_label = SEVERITY_LABELS.get(event.severity.lower(), event.severity.upper())
    lines = [
        f"Emergenzix · {event_label}",
        f"{event.symbol} · {event.timeframe} · {severity_label}",
    ]
    if event.event_type == "shadow_signal":
        lines.extend(
            [
                f"动作：{details.get('signal', '-')}",
                f"决策K线：{details.get('completed_at', '-')}",
                f"原因：{details.get('reason', '-')}",
                f"参考收盘价：{details.get('reference_close', '-')}",
                "执行假设：信号确认后的下一根已完成K线开盘模拟成交",
                (
                    "成本假设：手续费 {fee} bps；滑点 {slippage} bps".format(
                        fee=details.get("fee_bps", "-"),
                        slippage=details.get("slippage_bps", "-"),
                    )
                ),
                f"策略版本：{details.get('strategy_version_id', '-')}",
                f"信号ID：{details.get('alert_key', '-')}",
                "状态：PAPER；尚未完成30天观察；非投资建议。",
            ]
        )
        return "\n".join(lines)
    if event.event_type == "shadow_fill":
        lines.extend(
            [
                f"方向：{details.get('fill_side', '-')}",
                f"模拟价格：{details.get('fill_price', '-')}",
                f"模拟数量：{details.get('fill_quantity', '-')}",
                f"费用：{details.get('fee', '-')}",
                f"持仓：{details.get('position_quantity', '-')}",
                f"PAPER权益：{details.get('equity', '-')}",
                f"事件ID：{details.get('alert_key', '-')}",
                "状态：仅模拟成交；不代表用户实际成交；非投资建议。",
            ]
        )
        return "\n".join(lines)
    if event.event_type == "shadow_data_failure":
        lines.extend(
            [
                f"预期K线：{details.get('expected_completed_at', '-')}",
                f"错误码：{details.get('error_code', '-')}",
                f"事件ID：{details.get('alert_key', '-')}",
                "状态：本周期停止产生新模拟动作；等待数据恢复。",
            ]
        )
        return "\n".join(lines)
    if event.event_type == "ai_research_report":
        prediction = details.get("prediction", {})
        portfolio = details.get("portfolio", {})
        risk = details.get("risk", {})
        evaluation = details.get("evaluation", {})
        microstructure = details.get("microstructure", {})
        flow = microstructure.get("flow", {}) if isinstance(microstructure, dict) else {}
        liquidity = microstructure.get("liquidity", {}) if isinstance(microstructure, dict) else {}
        market_state = microstructure.get("state", {}) if isinstance(microstructure, dict) else {}
        risks = details.get("risks", [])
        limitations = details.get("limitations", [])
        lines.extend(
            [
                f"完成K线：{details.get('completed_at', '-')}",
                f"策略：{details.get('strategy_name', '-')} · {details.get('strategy_version', '-')}",
                f"策略动作：{details.get('decision', '-')}",
                f"策略理由：{details.get('decision_reason', '-')}",
                f"模型预测：{prediction.get('direction', '-')} · 周期 {prediction.get('horizon', '-')}",
                f"当前持仓：{portfolio.get('position', '-')} · 敞口 {portfolio.get('exposure_pct', '-')}%",
                f"风险状态：{risk.get('status', '-')}",
                f"市场战场：{market_state.get('label', '-')}",
                "主动流：{direction} · 净额 {signed:.2f}".format(
                    direction=flow.get("direction", "-"),
                    signed=float(flow.get("signed_notional", 0.0)),
                ),
                "深度：{pressure} · 买卖失衡 {imbalance:.3f}".format(
                    pressure=liquidity.get("pressure", "-"),
                    imbalance=float(liquidity.get("depth_imbalance", 0.0)),
                ),
                "潜在支撑：{support} · 潜在阻力：{resistance}".format(
                    support=(liquidity.get("support") or {}).get("price", "-"),
                    resistance=(liquidity.get("resistance") or {}).get("price", "-"),
                ),
                f"PAPER/回测偏差：{evaluation.get('paper_vs_backtest_deviation', '-')}",
                "独立决策：{current}/{minimum}".format(
                    current=evaluation.get("independent_decisions", "-"),
                    minimum=evaluation.get("minimum_decisions_before_change", "-"),
                ),
                f"DeepSeek二次判断：{details.get('summary', '-')}",
            ]
        )
        if isinstance(risks, list) and risks:
            lines.append("复核风险：" + "；".join(str(item) for item in risks))
        if isinstance(limitations, list) and limitations:
            lines.append("复核限制：" + "；".join(str(item) for item in limitations))
        lines.extend(
            [
                "DeepSeek角色：二次判断与模板化汇报",
                f"报告ID：{details.get('report_id', '-')}",
                "权限：不能生成或修改策略信号，不能创建 PAPER/TESTNET/LIVE 订单。",
                "状态：上游 AWS 计算结果的自动汇报；不构成投资建议，不保证收益。",
            ]
        )
        return "\n".join(lines)
    if event.event_type == "ai_market_analysis":
        model = str(details.get("model", "DeepSeek"))
        completed_at = str(details.get("last_completed_at", "-"))
        summary = str(details.get("summary", event.message))
        risks = details.get("risks", [])
        lines.extend(
            [
                f"模型：{model}",
                f"完成K线：{completed_at}",
                f"摘要：{summary}",
            ]
        )
        if isinstance(risks, list) and risks:
            lines.append("风险：" + "；".join(str(item) for item in risks[:3]))
        lines.append("模式：仅市场研究；本通知不会创建任何订单。")
        return "\n".join(lines)
    if scenario_label:
        lines.append(f"当前场景：{scenario_label}")
    if isinstance(levels, dict) and levels:
        lines.append(
            "关键位置：支撑={support}　压力={resistance}　ATR={atr}".format(
                support=levels.get("support", "-"),
                resistance=levels.get("resistance", "-"),
                atr=levels.get("atr", "-"),
            )
        )
    trigger = details.get("trigger") if isinstance(details, dict) else None
    invalidation = details.get("invalidation") if isinstance(details, dict) else None
    if trigger:
        lines.append(f"触发条件：{_translate_instruction(trigger)}")
    if invalidation:
        lines.append(f"失效条件：{_translate_instruction(invalidation)}")
    if not scenario_label:
        lines.append(f"说明：{event.message}")
    lines.append("模式：仅 PAPER 模拟观察；本通知不会创建任何订单。")
    return "\n".join(lines)


@dataclass(frozen=True)
class TelegramNotifier:
    """Send Watch Agent events through Telegram's HTTPS Bot API."""

    bot_token: str
    chat_id: str
    timeout_seconds: float = 10.0
    requester: Callable[..., object] = urllib.request.urlopen

    def __post_init__(self) -> None:
        if not self.bot_token.strip() or not self.chat_id.strip():
            raise ValueError("Telegram bot token and chat ID must both be configured")

    def send(self, event: WatchEvent, image: bytes | None = None) -> None:
        if image is None:
            body = json.dumps(
                {"chat_id": self.chat_id, "text": format_watch_event(event), "disable_web_page_preview": True}
            ).encode("utf-8")
            endpoint = "sendMessage"
            content_type = "application/json"
        else:
            boundary = "emergenzix-watch-boundary"
            fields = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{self.chat_id}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{format_watch_event(event)}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"emergenzix.png\"\r\n"
                "Content-Type: image/png\r\n\r\n"
            ).encode("utf-8")
            body = fields + image + f"\r\n--{boundary}--\r\n".encode("ascii")
            endpoint = "sendPhoto"
            content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/{endpoint}",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            response = self.requester(request, timeout=self.timeout_seconds)
            payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise NotificationError("Telegram delivery failed") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise NotificationError("Telegram rejected or returned an invalid response")
