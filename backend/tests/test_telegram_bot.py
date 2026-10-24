from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from emergenzix_api.telegram_bot import TelegramBotError, TelegramCommandBot


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self.body


def _record() -> dict[str, object]:
    return {
        "id": "a" * 64,
        "report": {
            "period": {
                "test_start_at": "2025-10-21T00:00:00Z",
                "test_end_at_exclusive": "2025-10-28T00:00:00Z",
            },
            "market": {"symbol": "BTC/USDT", "timeframe": "4h"},
            "strategy_selection": {
                "selected_candidate": {
                    "candidate_id": "sma-3-10",
                    "strategy_type": "sma_crossover",
                    "parameters": {"fast_window": 3, "slow_window": 10},
                },
                "status": "sufficient_validation_trades",
                "candidates": [
                    {
                        "candidate_id": "sma-3-10",
                        "strategy_type": "sma_crossover",
                        "parameters": {"fast_window": 3, "slow_window": 10},
                        "eligible": True,
                        "selection_score": 8.268134,
                        "total_return_pct": 8.9,
                        "max_drawdown_pct": 1.2,
                        "number_of_trades": 2,
                    },
                    {
                        "candidate_id": "sma-5-15",
                        "strategy_type": "sma_crossover",
                        "parameters": {"fast_window": 5, "slow_window": 15},
                        "eligible": True,
                        "selection_score": 7.2,
                        "total_return_pct": 7.5,
                        "max_drawdown_pct": 0.6,
                        "number_of_trades": 3,
                    },
                    {
                        "candidate_id": "donchian-20-10",
                        "strategy_type": "donchian_breakout",
                        "parameters": {"entry_window": 20, "exit_window": 10},
                        "eligible": False,
                        "selection_score": 2.3,
                        "total_return_pct": 2.0,
                        "max_drawdown_pct": 3.0,
                        "number_of_trades": 1,
                    },
                ],
            },
            "test_result": {
                "initial_cash": 10_000.0,
                "final_equity": 10_050.74,
                "net_pnl": 50.74,
                "total_return_pct": 0.5074,
                "max_drawdown_pct": 1.2,
                "number_of_trades": 2,
                "win_rate_pct": 100.0,
            },
            "cost_sensitivity": {
                "method": "fixed_fee_slippage_matrix_after_candidate_selection",
                "strategy_reselected_per_scenario": False,
                "test_window_changed_per_scenario": False,
                "stress_test_only": True,
                "scenarios": [
                    {
                        "fee_bps_per_side": 5,
                        "slippage_bps_per_side": 0,
                        "net_return_pct": 0.9102,
                        "net_pnl": 91.02,
                        "max_drawdown_pct": 1,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 5,
                        "slippage_bps_per_side": 5,
                        "net_return_pct": 0.8102,
                        "net_pnl": 81.02,
                        "max_drawdown_pct": 1.1,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 5,
                        "slippage_bps_per_side": 10,
                        "net_return_pct": 0.7102,
                        "net_pnl": 71.02,
                        "max_drawdown_pct": 1.2,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 10,
                        "slippage_bps_per_side": 0,
                        "net_return_pct": 0.6074,
                        "net_pnl": 60.74,
                        "max_drawdown_pct": 1,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 10,
                        "slippage_bps_per_side": 5,
                        "net_return_pct": 0.5074,
                        "net_pnl": 50.74,
                        "max_drawdown_pct": 1.2,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 10,
                        "slippage_bps_per_side": 10,
                        "net_return_pct": 0.4074,
                        "net_pnl": 40.74,
                        "max_drawdown_pct": 1.3,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 20,
                        "slippage_bps_per_side": 0,
                        "net_return_pct": 0.1074,
                        "net_pnl": 10.74,
                        "max_drawdown_pct": 1,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 20,
                        "slippage_bps_per_side": 5,
                        "net_return_pct": 0.0074,
                        "net_pnl": 0.74,
                        "max_drawdown_pct": 1.3,
                        "number_of_trades": 2,
                    },
                    {
                        "fee_bps_per_side": 20,
                        "slippage_bps_per_side": 10,
                        "net_return_pct": -0.0938,
                        "net_pnl": -9.38,
                        "max_drawdown_pct": 1.5,
                        "number_of_trades": 2,
                    },
                ],
            },
        },
    }


def _update(text: str, *, chat_id: int = 12345, update_id: int = 1) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id, "type": "private"}, "text": text},
    }


def _bot(
    tmp_path: Path,
    requester,
    *,
    clock=lambda: 1_000.0,
    cooldown: int = 60,
) -> TelegramCommandBot:
    return TelegramCommandBot(
        bot_token="12345:secret-token",
        allowed_chat_id="12345",
        api_base_url="http://api:8000",
        state_path=tmp_path / "telegram-state.json",
        requester=requester,
        clock=clock,
        backtest_cooldown_seconds=cooldown,
    )


def test_missing_token_or_allowlist_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="token is required"):
        TelegramCommandBot(
            bot_token="",
            allowed_chat_id="12345",
            state_path=tmp_path / "state.json",
        )
    with pytest.raises(ValueError, match="allowed chat ID"):
        TelegramCommandBot(
            bot_token="12345:secret-token",
            allowed_chat_id="",
            state_path=tmp_path / "state.json",
        )


def test_unauthorized_chat_is_ignored_without_network_requests(tmp_path: Path) -> None:
    calls = []

    def requester(request, *, timeout):
        calls.append(request.full_url)
        return FakeResponse({"ok": True, "result": {}})

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/status", chat_id=99999))
    assert calls == []


def test_group_chat_is_rejected_even_if_chat_id_matches(tmp_path: Path) -> None:
    calls = []

    def requester(request, *, timeout):
        calls.append(request.full_url)
        return FakeResponse({"ok": True, "result": {}})

    bot = _bot(tmp_path, requester)
    update = _update("/status")
    update["message"]["chat"]["type"] = "group"
    bot.handle_update(update)
    assert calls == []


def test_help_command_sends_usage_to_authorized_chat(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        assert request.full_url.endswith("/sendMessage")
        sent.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse({"ok": True, "result": {"message_id": 10}})

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/help@emergenzix_bot"))
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "12345"
    assert "/status" in sent[0]["text"]
    assert "/backtest YYYY-MM-DD [days]" in sent[0]["text"]
    assert "不会创建订单" in sent[0]["text"]


def test_status_reports_paper_mode_and_live_disabled(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/health"):
            return FakeResponse({
                "status": "ok",
                "mode": "PAPER",
                "live_trading_enabled": False,
            })
        if url.endswith("/ready"):
            return FakeResponse({"status": "ready"})
        if url.endswith("/api/v1/notifications/status"):
            return FakeResponse({
                "status": "ok",
                "metrics": {"pending": 2, "dead": 0},
            })
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 11}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/status"))
    assert "模式：PAPER" in sent[0]
    assert "LIVE 实盘交易：disabled" in sent[0]
    assert "pending 2, dead 0" in sent[0]


def test_backtest_posts_fixed_research_assumptions_and_formats_summary(
    tmp_path: Path,
) -> None:
    sent = []
    posted = []

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/api/v1/research/date-range-backtests") and request.get_method() == "POST":
            posted.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(_record())
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 12}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/backtest 2025-10-21 7"))
    assert posted == [{
        "start_date": "2025-10-21",
        "duration_days": 7,
        "training_days": 90,
        "initial_cash": 10_000.0,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
    }]
    assert "sma-3-10" in sent[0]
    assert "净盈亏：+50.74 USDT" in sent[0]
    assert "净收益率：+0.5074%" in sent[0]
    assert "最大回撤：1.2000%" in sent[0]
    assert "没有创建任何订单" in sent[0]


def test_backtest_cooldown_blocks_duplicate_requests(tmp_path: Path) -> None:
    sent = []
    posted = []

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/api/v1/research/date-range-backtests") and request.get_method() == "POST":
            posted.append(url)
            return FakeResponse(_record())
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 13}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester, clock=lambda: 1_000.0, cooldown=60)
    bot.handle_update(_update("/backtest 2025-10-21", update_id=1))
    bot.handle_update(_update("/backtest 2025-10-21", update_id=2))
    assert len(posted) == 1
    assert "回测冷却中" in sent[1]


def test_invalid_backtest_duration_does_not_call_research_api(tmp_path: Path) -> None:
    sent = []
    api_calls = []

    def requester(request, *, timeout):
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 14}})
        api_calls.append(request.full_url)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/backtest 2025-10-21 31"))
    assert "用法：" in sent[0]
    assert api_calls == []


def test_backtest_summary_includes_daily_pnl_table_from_equity_curve() -> None:
    record = _record()
    report = record["report"]
    report["period"]["test_start_at"] = "2025-10-21T00:00:00Z"
    report["period"]["test_end_at_exclusive"] = "2025-10-23T00:00:00Z"
    report["period"]["duration_days"] = 2
    curve = [
        {"timestamp": "2025-10-20T20:00:00Z", "equity": 10_000.0},
        {"timestamp": "2025-10-21T00:00:00Z", "equity": 10_010.0},
        {"timestamp": "2025-10-21T04:00:00Z", "equity": 10_020.0},
        {"timestamp": "2025-10-21T08:00:00Z", "equity": 10_030.0},
        {"timestamp": "2025-10-21T12:00:00Z", "equity": 10_040.0},
        {"timestamp": "2025-10-21T16:00:00Z", "equity": 10_050.0},
        {"timestamp": "2025-10-21T20:00:00Z", "equity": 10_100.0},
        {"timestamp": "2025-10-22T00:00:00Z", "equity": 10_090.0},
        {"timestamp": "2025-10-22T04:00:00Z", "equity": 10_080.0},
        {"timestamp": "2025-10-22T08:00:00Z", "equity": 10_070.0},
        {"timestamp": "2025-10-22T12:00:00Z", "equity": 10_060.0},
        {"timestamp": "2025-10-22T16:00:00Z", "equity": 10_055.0},
        {"timestamp": "2025-10-22T20:00:00Z", "equity": 10_050.0},
    ]
    report["test_result"]["equity_curve"] = curve

    from emergenzix_api.telegram_bot import format_backtest_record

    text = format_backtest_record(record)
    assert "每日收益明细（UTC；由 4 小时权益曲线计算）" in text
    assert "2025-10-21 | +100.00 USDT | +1.0000% | 10,100.00 USDT" in text
    assert "2025-10-22 | -50.00 USDT | -0.4950% | 10,050.00 USDT" in text


def test_backtest_summary_includes_fixed_cost_sensitivity_matrix() -> None:
    from emergenzix_api.telegram_bot import format_backtest_record

    text = format_backtest_record(_record())

    assert "交易成本敏感性（手续费/滑点均为单边；策略与测试区间固定）" in text
    assert "手续费/滑点 | 净收益率 | 净盈亏 | 最大回撤 | 交易数" in text
    assert "5/0 bps | +0.9102% | +91.02 USDT | 1.0000% | 2" in text
    assert "10/5 bps | +0.5074% | +50.74 USDT | 1.2000% | 2" in text
    assert "20/10 bps | -0.0938% | -9.38 USDT | 1.5000% | 2" in text


def test_backtest_summary_includes_candidate_selection_evidence() -> None:
    from emergenzix_api.telegram_bot import format_backtest_record

    text = format_backtest_record(_record())

    assert "策略候选验证（仅训练期数据；不代表测试期结果）" in text
    assert "候选 | 验证收益 | 最大回撤 | 交易数 | 选择分 | 状态" in text
    assert "sma-3-10 | +8.9000% | 1.2000% | 2 | +8.2681 | 已选" in text
    assert "sma-5-15 | +7.5000% | 0.6000% | 3 | +7.2000 | 达标" in text
    assert "donchian-20-10 | +2.0000% | 3.0000% | 1 | +2.3000 | 证据不足" in text


def test_malformed_candidate_selection_table_is_omitted() -> None:
    from emergenzix_api.telegram_bot import format_backtest_record

    record = _record()
    candidates = record["report"]["strategy_selection"]["candidates"]
    candidates[1]["candidate_id"] = "sma-3-10"

    text = format_backtest_record(record)

    assert "策略候选验证" not in text
    assert "净收益率：+0.5074%" in text


def test_malformed_cost_sensitivity_matrix_is_omitted() -> None:
    from emergenzix_api.telegram_bot import format_backtest_record

    record = _record()
    scenarios = record["report"]["cost_sensitivity"]["scenarios"]
    record["report"]["cost_sensitivity"]["scenarios"] = scenarios[:-1]

    text = format_backtest_record(record)

    assert "交易成本敏感性" not in text
    assert "净收益率：+0.5074%" in text


def test_incomplete_equity_curve_does_not_emit_daily_table() -> None:
    record = _record()
    report = record["report"]
    report["period"]["test_start_at"] = "2025-10-21T00:00:00Z"
    report["period"]["test_end_at_exclusive"] = "2025-10-22T00:00:00Z"
    report["test_result"]["equity_curve"] = [
        {"timestamp": "2025-10-20T20:00:00Z", "equity": 10_000.0},
        {"timestamp": "2025-10-21T20:00:00Z", "equity": 10_100.0},
    ]

    from emergenzix_api.telegram_bot import format_backtest_record

    text = format_backtest_record(record)
    assert "每日收益明细" not in text
    assert "净盈亏：+50.74 USDT" in text


def test_latest_returns_most_recent_saved_record(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        url = request.full_url
        if "/api/v1/research/date-range-backtests?limit=1" in url:
            return FakeResponse([_record()])
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 15}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/latest"))
    assert "历史回测完成" in sent[0]
    assert "sma-3-10" in sent[0]


def test_poll_once_discards_stale_updates_then_persists_offset(tmp_path: Path) -> None:
    state_path = tmp_path / "telegram-state.json"
    sent = []

    def requester(request, *, timeout):
        url = request.full_url
        if "/getUpdates?" in url and "offset=-1" in url:
            # A stale command pending before first startup must not be executed.
            return FakeResponse({
                "ok": True,
                "result": [_update("/backtest 2025-10-21", update_id=41)],
            })
        if "/getUpdates?" in url:
            assert "offset=42" in url
            return FakeResponse({
                "ok": True,
                "result": [_update("/help", update_id=42)],
            })
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 16}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.poll_once()
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first_state["last_update_id"] == 41
    assert first_state["poll_initialized"] is True
    assert sent == []

    bot.poll_once()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_update_id"] == 42
    assert len(sent) == 1
    assert "/status" in sent[0]


def test_telegram_transport_error_does_not_expose_bot_token(tmp_path: Path) -> None:
    def requester(_request, *, timeout):
        raise urllib.error.URLError("request URL included 12345:secret-token")

    bot = _bot(tmp_path, requester)
    with pytest.raises(TelegramBotError) as error:
        bot.handle_update(_update("/help"))
    assert "secret-token" not in str(error.value)
