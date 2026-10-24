from __future__ import annotations

import json
import urllib.error
import urllib.parse
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
    assert "/history" in sent[0]["text"]
    assert "/report RECORD_ID" in sent[0]["text"]
    assert "/compare ID1 ID2" in sent[0]["text"]
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
    events = []

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/api/v1/research/date-range-backtests") and request.get_method() == "POST":
            events.append("api")
            posted.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(_record())
        if url.endswith("/sendMessage"):
            events.append("telegram")
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
    assert events == ["telegram", "api", "telegram"]
    assert "已接受回测请求" in sent[0]
    summary = sent[-1]
    assert "sma-3-10" in summary
    assert "净盈亏：+50.74 USDT" in summary
    assert "净收益率：+0.5074%" in summary
    assert "最大回撤：1.2000%" in summary
    assert "没有创建任何订单" in summary

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
    assert "回测冷却中" in sent[-1]


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


def test_backtest_date_overflow_returns_usage_without_research_api(
    tmp_path: Path,
) -> None:
    sent = []
    api_calls = []

    def requester(request, *, timeout):
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 15}})
        api_calls.append(request.full_url)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/backtest 9999-12-31 30"))
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



def test_history_lists_five_max_and_full_ids_in_order(tmp_path: Path) -> None:
    sent = []
    api_requests = []
    recent = _record()
    older = _record()
    older["id"] = "b" * 64
    older["report"]["period"]["test_start_at"] = "2025-09-01T00:00:00Z"

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/api/v1/research/date-range-backtests?limit=5"):
            api_requests.append((request.get_method(), url))
            return FakeResponse([recent, older])
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 20}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/history"))
    assert len(api_requests) == 1
    assert api_requests[0][0] == "GET"
    assert len(sent) == 1
    assert sent[0].index("/report " + "a" * 64) < sent[0].index("/report " + "b" * 64)
    assert "sma-3-10" in sent[0]
    assert "+0.5074%" in sent[0]
    assert "\n/report " + "a" * 64 in sent[0]
    assert "没有创建任何订单" in sent[0]


def test_report_fetches_one_saved_record_without_rerun(tmp_path: Path) -> None:
    sent = []
    fetched = []
    record_id = "a" * 64

    def requester(request, *, timeout):
        url = request.full_url
        if url.endswith("/api/v1/research/date-range-backtests/" + record_id):
            fetched.append(request.get_method())
            return FakeResponse(_record())
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 21}})
        raise AssertionError(f"unexpected URL: {url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/report " + record_id))
    assert fetched == ["GET"]
    assert "历史回测完成" in sent[0]
    assert "净收益率：+0.5074%" in sent[0]


@pytest.mark.parametrize(
    "command",
    [
        "/report",
        "/report abc",
        "/report " + "A" * 64,
        "/report " + "a" * 63 + "/",
        "/report " + "a" * 64 + " extra",
        "/history extra",
    ],
)
def test_invalid_archive_commands_never_call_api(tmp_path: Path, command: str) -> None:
    sent = []

    def requester(request, *, timeout):
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 22}})
        raise AssertionError(f"invalid command called API: {request.full_url}")

    _bot(tmp_path, requester).handle_update(_update(command))
    assert len(sent) == 1
    assert "用法：" in sent[0]


def test_history_empty_and_malformed_responses_fail_closed(tmp_path: Path) -> None:
    sent = []
    payloads = [[], [{"id": "invalid"}]]

    def requester(request, *, timeout):
        if request.full_url.endswith("/api/v1/research/date-range-backtests?limit=5"):
            return FakeResponse(payloads.pop(0))
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 23}})
        raise AssertionError(f"unexpected URL: {request.full_url}")

    bot = _bot(tmp_path, requester)
    bot.handle_update(_update("/history"))
    bot.handle_update(_update("/history", update_id=2))
    assert "尚无已保存" in sent[0]
    assert "响应格式异常" in sent[1]
    assert "/report " not in sent[1]


def test_report_api_error_is_sanitized(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 24}})
        raise urllib.error.URLError("do not leak internal address")

    _bot(tmp_path, requester).handle_update(_update("/report " + "a" * 64))
    assert "查询历史报告失败" in sent[0]
    assert "do not leak" not in sent[0]


def test_unauthorized_history_is_ignored(tmp_path: Path) -> None:
    def requester(request, *, timeout):
        raise AssertionError(f"unauthorized query: {request.full_url}")

    _bot(tmp_path, requester).handle_update(_update("/history", chat_id=99999))


def _comparison_fixture(
    first_id: str = "a" * 64,
    second_id: str = "b" * 64,
    *,
    different_periods: bool = False,
    different_costs: bool = False,
) -> dict[str, object]:
    def row(record_id: str) -> dict[str, object]:
        return {
            "record_id": record_id,
            "selected_candidate": "sma-3-10",
            "strategy_type": "sma_crossover",
            "test_start_at": "2025-10-21T00:00:00Z",
            "test_end_at_exclusive": "2025-10-28T00:00:00Z",
            "dataset_sha256": "d" * 64,
            "net_return_pct": 0.5074,
            "benchmark_return_pct": 3.2,
            "max_drawdown_pct": 1.2,
            "number_of_trades": 2,
            "win_rate_pct": 50.0,
            "total_fees": 39.3,
            "fee_bps_per_side": 10.0,
            "slippage_bps_per_side": 5.0,
        }

    first, second = row(first_id), row(second_id)
    if different_periods:
        second["test_start_at"] = "2025-11-01T00:00:00Z"
        second["test_end_at_exclusive"] = "2025-11-08T00:00:00Z"
    if different_costs:
        second["fee_bps_per_side"] = 20.0
    return {
        "comparison_type": "independent_saved_observations",
        "controlled_head_to_head": False,
        "automatic_winner_selection": False,
        "different_test_periods": different_periods,
        "different_cost_assumptions": different_costs,
        "observations": [first, second],
    }


def test_compare_command_gets_two_saved_records_without_rerun(tmp_path: Path) -> None:
    sent, api_requests = [], []

    def requester(request, *, timeout):
        url = request.full_url
        if "/api/v1/research/date-range-comparisons?" in url:
            api_requests.append(request)
            return FakeResponse(_comparison_fixture())
        if url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 31}})
        raise AssertionError(f"unexpected URL: {url}")

    _bot(tmp_path, requester).handle_update(
        _update("/compare " + "a" * 64 + " " + "b" * 64)
    )
    assert len(api_requests) == 1
    assert api_requests[0].get_method() == "GET"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(api_requests[0].full_url).query)
    assert query["record_ids"] == ["a" * 64, "b" * 64]
    assert len(sent) == 1
    assert "净收益：+0.5074%" in sent[0]
    assert "最大回撤：1.2000%" in sent[0]
    assert "不自动选优或创建订单" in sent[0]


def test_compare_command_displays_cost_and_period_warnings(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        if "/date-range-comparisons?" in request.full_url:
            return FakeResponse(
                _comparison_fixture(different_periods=True, different_costs=True)
            )
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 32}})
        raise AssertionError(f"unexpected URL: {request.full_url}")

    _bot(tmp_path, requester).handle_update(
        _update("/compare " + "a" * 64 + " " + "b" * 64)
    )
    assert "测试区间不同" in sent[0]
    assert "手续费或滑点假设不同" in sent[0]


@pytest.mark.parametrize(
    "argument",
    [
        "/compare",
        "/compare " + "a" * 64,
        "/compare " + "a" * 64 + " " + "a" * 64,
        "/compare " + "a" * 64 + " " + "B" * 64,
        "/compare " + "a" * 64 + " " + "../etc/passwd",
        "/compare " + "a" * 64 + " " + "b" * 64 + " extra",
    ],
)
def test_compare_rejects_invalid_requests_without_api_call(tmp_path: Path, argument: str) -> None:
    sent = []

    def requester(request, *, timeout):
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 33}})
        raise AssertionError(f"should not call API: {request.full_url}")

    _bot(tmp_path, requester).handle_update(_update(argument))
    assert len(sent) == 1
    assert "用法：/compare" in sent[0]


def test_compare_fails_closed_if_api_response_is_not_independent(tmp_path: Path) -> None:
    sent = []
    bad = _comparison_fixture()
    bad["automatic_winner_selection"] = True

    def requester(request, *, timeout):
        if "/date-range-comparisons?" in request.full_url:
            return FakeResponse(bad)
        if request.full_url.endswith("/sendMessage"):
            sent.append(json.loads(request.data.decode("utf-8"))["text"])
            return FakeResponse({"ok": True, "result": {"message_id": 34}})
        raise AssertionError(f"unexpected URL: {request.full_url}")

    _bot(tmp_path, requester).handle_update(
        _update("/compare " + "a" * 64 + " " + "b" * 64)
    )
    assert "响应格式异常" in sent[0]
    assert "净收益：" not in sent[0]


def test_compare_mismatched_record_id_rejects_result(tmp_path: Path) -> None:
    from emergenzix_api.telegram_bot import format_saved_comparison

    comparison = _comparison_fixture()
    assert "响应格式异常" in format_saved_comparison(
        comparison, ["c" * 64, "b" * 64]
    )


def test_compare_unauthorized_chat_is_silent(tmp_path: Path) -> None:
    def requester(request, *, timeout):
        raise AssertionError(f"unauthorized call: {request.full_url}")

    _bot(tmp_path, requester).handle_update(
        _update("/compare " + "a" * 64 + " " + "b" * 64, chat_id=99999)
    )

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



def test_telegram_message_split_is_lossless_and_unicode_safe() -> None:
    from emergenzix_api.telegram_bot import (
        _split_telegram_text,
        _utf16_units,
    )

    message = ("2025-10-21 | 盈亏 +12.00 | 🚀🚀\n" * 300) + "最终风险声明"
    parts = _split_telegram_text(message)
    assert parts is not None
    assert len(parts) > 1
    assert "".join(parts) == message
    assert all(_utf16_units(part) <= 3300 for part in parts)


def test_long_telegram_report_is_sent_in_order_without_truncation(tmp_path: Path) -> None:
    from emergenzix_api.telegram_bot import _utf16_units

    sent = []

    def requester(request, *, timeout):
        assert request.full_url.endswith("/sendMessage")
        sent.append(json.loads(request.data.decode("utf-8"))["text"])
        return FakeResponse({"ok": True, "result": {"message_id": len(sent)}})

    body = ("2025-10-21 | +1.00 USDT | +0.01% | 🚀\n" * 240) + "最终声明：没有创建任何订单。"
    _bot(tmp_path, requester)._send_message("12345", body)

    assert len(sent) > 1
    assert all(_utf16_units(part) <= 3900 for part in sent)
    assert all("仅历史 PAPER 研究，不创建交易订单" in part for part in sent)
    assert sent[0].startswith(f"【报告 1/{len(sent)}】")
    assert sent[-1].startswith(f"【报告 {len(sent)}/{len(sent)}】")
    assert "最终声明：没有创建任何订单。" in sent[-1]
    assert all("内容已截断" not in part for part in sent)


def test_oversized_telegram_report_refuses_partial_delivery(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        sent.append(json.loads(request.data.decode("utf-8"))["text"])
        return FakeResponse({"ok": True, "result": {"message_id": 42}})

    _bot(tmp_path, requester)._send_message("12345", "RESEARCH_PAYLOAD_" * 4000)
    assert len(sent) == 1
    assert "安全分段发送上限" in sent[0]
    assert "RESEARCH_PAYLOAD_" not in sent[0]
    assert "不创建订单" in sent[0]


def test_short_telegram_message_remains_single_unchanged_send(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        sent.append(json.loads(request.data.decode("utf-8"))["text"])
        return FakeResponse({"ok": True, "result": {"message_id": 43}})

    _bot(tmp_path, requester)._send_message("12345", "简短的状态消息")
    assert sent == ["简短的状态消息"]


def test_multipart_send_error_is_sanitized_and_stops_delivery(tmp_path: Path) -> None:
    sent = []

    def requester(request, *, timeout):
        sent.append(request.full_url)
        if len(sent) == 2:
            raise urllib.error.URLError("12345:secret-token")
        return FakeResponse({"ok": True, "result": {"message_id": 44}})

    bot = _bot(tmp_path, requester)
    with pytest.raises(TelegramBotError) as error:
        bot._send_message("12345", "已保存实验说明\n" * 1400)
    assert len(sent) == 2
    assert "secret-token" not in str(error.value)


def test_telegram_transport_error_does_not_expose_bot_token(tmp_path: Path) -> None:
    def requester(_request, *, timeout):
        raise urllib.error.URLError("request URL included 12345:secret-token")

    bot = _bot(tmp_path, requester)
    with pytest.raises(TelegramBotError) as error:
        bot.handle_update(_update("/help"))
    assert "secret-token" not in str(error.value)
