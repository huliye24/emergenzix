from __future__ import annotations

import pytest

from emergenzix_api.telegram_credentials import (
    TelegramCredentialError,
    load_telegram_bot_token,
)


def test_token_file_is_read_without_changing_its_contents(tmp_path, monkeypatch):
    secret = tmp_path / "telegram-token"
    secret.write_text("123456:secret-token\n", encoding="utf-8")
    monkeypatch.delenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE", str(secret))

    assert load_telegram_bot_token() == "123456:secret-token"
    assert secret.read_text(encoding="utf-8") == "123456:secret-token\n"


def test_direct_and_file_token_conflict_fails_without_secret_in_error(
    tmp_path, monkeypatch
):
    secret = tmp_path / "telegram-token"
    secret.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "direct-secret")
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE", str(secret))

    with pytest.raises(TelegramCredentialError) as error:
        load_telegram_bot_token()

    assert "direct-secret" not in str(error.value)
    assert "file-secret" not in str(error.value)


def test_missing_or_empty_token_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv(
        "EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE", str(tmp_path / "missing")
    )
    with pytest.raises(TelegramCredentialError, match="unavailable"):
        load_telegram_bot_token()

    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("EMERGENZIX_TELEGRAM_BOT_TOKEN_FILE", str(empty))
    with pytest.raises(TelegramCredentialError, match="invalid"):
        load_telegram_bot_token()
