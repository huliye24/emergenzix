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
        if payload.get("ok") is not True:
            raise NotificationError("Telegram rejected the notification")
