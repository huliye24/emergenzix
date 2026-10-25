"""Regression and data-safety checks for the pure Telegram backtest formatter."""

from __future__ import annotations

import math

from emergenzix_api.telegram_backtest_format import (
    _finite_number,
    format_backtest_record as pure_formatter,
)
from emergenzix_api.telegram_bot import format_backtest_record as legacy_formatter


def _saved_record() -> dict:
    return {
        "id": "c" * 64,
        "report": {
            "period": {
                "test_start_at": "2025-10-21T00:00:00Z",
                "test_end_at_exclusive": "2025-10-28T00:00:00Z",
            },
            "market": {"symbol": "BTC/USDT", "timeframe": "4h"},
            "strategy_selection": {
                "status": "sufficient_validation_trades",
                "selected_candidate": {
                    "candidate_id": "sma-3-10",
                    "strategy_type": "sma_crossover",
                    "parameters": {"fast_window": 3, "slow_window": 10},
                },
            },
            "test_result": {
                "net_pnl": 50.74,
                "total_return_pct": 0.5074,
                "max_drawdown_pct": 1.2,
                "number_of_trades": 2,
                "win_rate_pct": 100.0,
            },
        },
    }


def test_legacy_public_import_is_same_pure_formatter() -> None:
    assert legacy_formatter is pure_formatter
    expected = pure_formatter(_saved_record())
    assert expected == legacy_formatter(_saved_record())
    assert "净收益率：+0.5074%" in expected
    assert "净盈亏：+50.74 USDT" in expected
    assert "安全：仅历史 PAPER 模拟" in expected


def test_boolean_nan_inf_are_never_printed_as_financial_returns() -> None:
    record = _saved_record()
    metrics = record["report"]["test_result"]
    metrics["net_pnl"] = True
    metrics["total_return_pct"] = False
    metrics["max_drawdown_pct"] = math.nan
    metrics["win_rate_pct"] = math.inf
    text = pure_formatter(record)
    assert "净盈亏：N/A" in text
    assert "净收益率：N/A" in text
    assert "最大回撤：N/A" in text
    assert "胜率：N/A" in text
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()
    assert _finite_number(True) is None
    assert _finite_number(False) is None
    assert _finite_number(float("nan")) is None
    assert _finite_number(float("inf")) is None


def test_malformed_text_cannot_inject_forged_telegram_report_lines() -> None:
    record = _saved_record()
    data = record["report"]
    data["period"]["test_start_at"] += "\n伪造日期：now"
    data["market"]["symbol"] = "BTC/USDT\n伪造市场：LIVE"
    selected = data["strategy_selection"]["selected_candidate"]
    selected["candidate_id"] = "sma-3-10\n伪造收益：+999%"
    selected["strategy_type"] = "sma_crossover\n伪造交易：ON"
    selected["parameters"] = {"fast\n伪造参数": "3\n伪造路径", 1: 2}
    data["strategy_selection"]["status"] = "unknown\n伪造核准：YES"
    data["test_result"]["number_of_trades"] = "2\n伪造订单：100"
    text = pure_formatter(record)
    assert all(not line.startswith("伪造") for line in text.splitlines())
    assert text.count("Emergenzix · 历史回测完成") == 1
    assert "安全：仅历史 PAPER 模拟" in text
    assert "净收益率：+0.5074%" in text


def test_extremely_long_untrusted_identifiers_are_bounded() -> None:
    record = _saved_record()
    selected = record["report"]["strategy_selection"]["selected_candidate"]
    selected["candidate_id"] = "x" * 50_000
    selected["parameters"] = {"value": "y" * 50_000}
    output = pure_formatter(record)
    assert len(output) < 2000
    assert "x" * 81 not in output
    assert "y" * 241 not in output


def test_malformed_or_missing_report_returns_safe_error_text() -> None:
    assert pure_formatter(None) == "回测记录格式无法识别，请检查 API。"
    assert pure_formatter({}) == "回测记录缺少报告内容，请检查 API。"
    assert "缺少必要字段" in pure_formatter({"report": {"market": {}}})
