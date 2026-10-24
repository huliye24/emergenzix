"""Outbound notification adapters. Secrets are read by callers and never logged."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .watch_agent import WatchEvent


class NotificationError(RuntimeError):
    """Raised when a notification cannot be delivered without exposing credentials."""


def format_watch_event(event: WatchEvent) -> str:
    """Render a compact human-readable alert from a structured watch event."""
    details = event.details
    levels = details.get("key_levels", {}) if isinstance(details, dict) else {}
    scenario = details.get("scenario") if isinstance(details, dict) else None
    lines = [
        f"Emergenzix · {event.event_type}",
        f"{event.symbol} · {event.timeframe} · {event.severity.upper()}",
        event.message,
    ]
    if scenario:
        lines.append(f"Scenario: {scenario}")
    if isinstance(levels, dict) and levels:
        lines.append(
            "Levels: support={support} resistance={resistance} ATR={atr}".format(
                support=levels.get("support", "-"),
                resistance=levels.get("resistance", "-"),
                atr=levels.get("atr", "-"),
            )
        )
    trigger = details.get("trigger") if isinstance(details, dict) else None
    invalidation = details.get("invalidation") if isinstance(details, dict) else None
    if trigger:
        lines.append(f"Trigger: {trigger}")
    if invalidation:
        lines.append(f"Invalidation: {invalidation}")
    lines.append("Mode: PAPER review only; no order was created.")
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

    def send(self, event: WatchEvent) -> None:
        body = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": format_watch_event(event),
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self.requester(request, timeout=self.timeout_seconds)
            payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise NotificationError("Telegram delivery failed") from exc
        if payload.get("ok") is not True:
            raise NotificationError("Telegram rejected the notification")
