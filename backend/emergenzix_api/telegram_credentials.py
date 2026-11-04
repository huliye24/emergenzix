"""Fail-closed Telegram credential loading without secret disclosure."""

from __future__ import annotations

import os
from pathlib import Path


class TelegramCredentialError(ValueError):
    """Telegram credentials are missing, conflicting, or unreadable."""


def load_telegram_bot_token() -> str:
    """Load the bot token from one environment value or one mounted file."""
    direct = os.getenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "").strip()
    file_value = os.getenv("EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE", "").strip()
    if direct and file_value:
        raise TelegramCredentialError(
            "configure exactly one Telegram bot token source"
        )
    if direct:
        return _validate_token(direct)
    if not file_value:
        return ""
    try:
        token = Path(file_value).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TelegramCredentialError("Telegram bot token file is unavailable") from exc
    return _validate_token(token)


def _validate_token(token: str) -> str:
    if not token or len(token) > 256 or "\x00" in token or "\n" in token or "\r" in token:
        raise TelegramCredentialError("Telegram bot token is invalid")
    return token
