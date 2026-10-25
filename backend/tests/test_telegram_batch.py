"""Telegram batch commands: private opt-in, idempotent registration, verified reports."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from emergenzix_api.database import Database
from emergenzix_api.research_batch_plan import (
    WeeklyResearchBatchPlanRequest, plan_weekly_research_batch,
)
from emergenzix_api.research_batch_state import (
    claim_next_research_job, get_research_batch,
)
from emergenzix_api.research_batch_worker import run_one_batch_job
from emergenzix_api.schemas import Bar
from emergenzix_api.telegram_batch import handle_batch_message
from emergenzix_api.telegram_bot import TelegramCommandBot

DATES = ("2025-10-21", "2025-10-28")


def _plan(*dates):
    return plan_weekly_research_batch(
        WeeklyResearchBatchPlanRequest(start_dates=list(dates))
    )


def _update(command: str, chat: int = 1234, kind: str = "private") -> dict:
    return {
        "update_id": 19,
        "message": {"chat": {"id": chat, "type": kind}, "text": command},
    }


class _Response:
    def __init__(self):
        self.payload = b'{"ok":true,"result":{"message_id":42}}'

    def read(self):
        return self.payload


def _bot(tmp_path, requester, *, enabled: bool, db: Path | None):
    return TelegramCommandBot(
        bot_token="123456:test-secret",
        allowed_chat_id="1234",
        state_path=tmp_path / "bot-state.json",
        api_base_url="http://api:8000",
        requester=requester,
        batch_commands_enabled=enabled,
        batch_db_path=db,
    )


def _sent(requests, text):
    def fake_request(request, *, timeout):
        assert request.full_url.endswith("/sendMessage")
        data = json.loads(request.data.decode("utf-8"))
        assert data["chat_id"] == "1234"
        requests.append(data["text"])
        return _Response()
    return fake_request


def test_default_disabled_batch_does_not_open_or_create_database(tmp_path) -> None:
    sent = []
    db = tmp_path / "absent.db"
    bot = _bot(tmp_path, _sent(sent, ""), enabled=False, db=db)
    bot.handle_update(_update("/batch register 2025-10-21"))
    assert len(sent) == 1
    assert "尚未启用" in sent[0]
    assert not db.exists()


def test_batch_feature_requires_explicit_db_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="explicit shared SQLite"):
        _bot(tmp_path, _sent([], ""), enabled=True, db=None)


def test_telegram_private_allowlist_blocks_unauthorized_mutations(tmp_path) -> None:
    db = tmp_path / "shared.db"
    Database(str(db))
    sent = []
    bot = _bot(tmp_path, _sent(sent, ""), enabled=True, db=db)
    bot.handle_update(_update("/batch register 2025-10-21", chat=9988))
    bot.handle_update(_update("/batch register 2025-10-21", kind="group"))
    assert sent == []
    with Database(str(db)).connection() as conn:
        assert conn.execute("SELECT count(*) FROM research_batch_plans").fetchone()[0] == 0


def test_telegram_batch_plan_register_status_round_trip(tmp_path) -> None:
    db_path = tmp_path / "emergenzix.db"
    Database(str(db_path))
    sent = []
    bot = _bot(tmp_path, _sent(sent, ""), enabled=True, db=db_path)
    bot.handle_update(_update("/batch plan 2025-10-28 2025-10-21"))
    assert "尚未登记" in sent[-1]
    batch_id = _plan(*DATES)["batch_id"]
    assert batch_id in sent[-1]
    with Database(str(db_path)).connection() as conn:
        assert conn.execute("SELECT count(*) FROM research_batch_jobs").fetchone()[0] == 0

    bot.handle_update(_update("/batch register 2025-10-28 2025-10-21"))
    assert "登记成功" in sent[-1]
    assert batch_id in sent[-1]
    assert "不会" not in sent[-1] or "任务" in sent[-1]
    snapshot = get_research_batch(Database(str(db_path)), batch_id)
    assert [j["state"] for j in snapshot["jobs"]] == ["pending", "pending"]

    lease = claim_next_research_job(
        Database(str(db_path)), batch_id, worker_id="internal-worker",
    )
    assert lease["sequence"] == 1
    bot.handle_update(_update("/batch register 2025-10-21 2025-10-28"))
    assert "登记成功" in sent[-1]
    assert get_research_batch(Database(str(db_path)), batch_id)["jobs"][0]["attempts"] == 1

    bot.handle_update(_update(f"/batch status {batch_id}"))
    assert "0/2 周" in sent[-1]
    assert "执行中 1" in sent[-1]
    assert "待执行 1" in sent[-1]
    assert "lease_token" not in sent[-1]
    assert lease["lease_token"] not in sent[-1]

    bot.handle_update(_update(f"/batch report {batch_id}"))
    assert "尚无已完成" in sent[-1]
    assert "不能累乘" in sent[-1]

    with Database(str(db_path)).connection() as conn:
        assert conn.execute("SELECT count(*) FROM research_batch_plans").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM research_batch_jobs").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM date_range_backtest_records").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0


def test_register_rejects_nonexistent_shared_database(tmp_path) -> None:
    path = tmp_path / "missing" / "not-created.db"
    text = handle_batch_message(
        ["register", "2025-10-21"], db_path=path,
    )
    assert "尚未就绪" in text
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("arguments", [
    ["register", "2025-10-21", "2025-10-21"],
    ["register", "2025-10-21", "2025-10-22"],
    ["plan", "2999-01-01"],
    ["plan", "2025/10/21"],
    ["plan", "2025-10-21T00:00:00Z"],
    ["plan", *["2025-10-21"] * 13],
])
def test_invalid_plans_fail_without_created_database(tmp_path, arguments):
    path = tmp_path / "new" / "database.db"
    result = handle_batch_message(arguments, db_path=path)
    assert "无效" in result or "最多 12" in result
    assert not path.exists()


@pytest.mark.parametrize("command", ["run", "execute", "trade", "live", "retry", "cancel"])
def test_telegram_batch_never_exposes_execution_or_trading(tmp_path, command):
    path = tmp_path / "db.sqlite"
    result = handle_batch_message([command, "2025-10-21"], db_path=path)
    assert "不支持执行或交易控制" in result
    assert not path.exists()


def test_status_requires_canonical_batch_id_and_is_readonly(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite"
    Database(str(path))
    text = handle_batch_message(["status", "../../etc/passwd"], db_path=path)
    assert "用法" not in text or "BATCH_ID" in text
    assert "批次研究命令" in text
    with pytest.raises(ValueError, match="no init"):
        def fail(*args, **kwargs):
            raise ValueError("no init")
        fail()
    monkeypatch.setattr(Database, "initialize", lambda *_: (_ for _ in ()).throw(AssertionError("No DB init")))
    msg = handle_batch_message(["status", "a" * 64], db_path=path)
    assert "不可用" in msg
    assert "订单" in msg


def _offline_history(monkeypatch):
    import emergenzix_api.date_range_backtest as engine

    def fake_archive(*, start_at: datetime, end_at: datetime):
        count = int((end_at - start_at).total_seconds() // (4 * 3600)) + 1
        bars = []
        for i in range(count):
            close = 80_000 + 2_500 * math.sin(i * 0.11) + i * 3
            opened = close * (1 + 0.0005 * math.sin(i * 0.07))
            bars.append(Bar(
                timestamp=start_at + timedelta(hours=4 * i),
                open=opened,
                high=max(opened, close) * 1.003,
                low=min(opened, close) * 0.997,
                close=close,
                volume=1000 + i,
            ).model_dump(mode="json"))
        return bars

    monkeypatch.setattr(engine, "fetch_binance_btc_4h_archive", fake_archive)


def test_verified_report_includes_real_offline_completed_week(
    tmp_path, monkeypatch,
):
    _offline_history(monkeypatch)
    path = tmp_path / "shared.sqlite"
    db = Database(str(path))
    result = handle_batch_message(
        ["register", "2025-10-21"], db_path=path,
    )
    batch_id = _plan("2025-10-21")["batch_id"]
    assert batch_id in result

    backtest = run_one_batch_job(db, batch_id, worker_id="internal")
    assert backtest["status"] == "completed"
    summary = handle_batch_message(["report", batch_id], db_path=path)
    assert "1/1 周" in summary
    assert "单周净收益" in summary
    assert "最大回撤" in summary
    assert "不能累乘" in summary
    assert backtest["record_id"] not in summary  # concise Telegram summary only
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0
