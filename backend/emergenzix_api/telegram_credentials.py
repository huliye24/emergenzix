"""Fail-closed Telegram credential loading without secret disclosure."""

from __future__ import annotations

import os
from pathlib import Path


class TelegramCredentialError(ValueError):
    """Telegram credentials are missing, conflicting, or unreadable."""


def load_telegram_bot_token() -> str:
    """Load the bot token from one environment value or one mounted file."""
    return _load_token(
        direct_name="EMERGENZIX_TELEGRAM_BOT_TOKEN",
        file_name="EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE",
    )


def load_follow_telegram_bot_token() -> str:
    """Load the independently configured follow-channel bot token."""
    return _load_token(
        direct_name="EMERGENZIX_FOLLOW_TELEGRAM_BOT_TOKEN",
        file_name="EMERGENZIX_FOLLOW_TELEGRAM_BOT_TOKEN_FILE",
    )


def _load_token(*, direct_name: str, file_name: str) -> str:
    direct = os.getenv(direct_name, "").strip()
    file_value = os.getenv(file_name, "").strip()
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
