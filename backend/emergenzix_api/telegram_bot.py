"""Allowlisted Telegram command interface for historical PAPER research only."""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


# Telegram limits text messages to 4,096 UTF-16 code units. Leave headroom
# for part headers and a safety note, including astral-plane emoji.
_TELEGRAM_SINGLE_MESSAGE_UNITS = 3900
_TELEGRAM_PART_BODY_UNITS = 3300
_TELEGRAM_MAX_PARTS = 12


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _split_telegram_text(text: str) -> list[str] | None:
    """Return lossless, ordered text parts; None means the message is too large."""
    if _utf16_units(text) <= _TELEGRAM_SINGLE_MESSAGE_UNITS:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        if len(parts) >= _TELEGRAM_MAX_PARTS:
            return None
        units = 0
        end = start
        while end < len(text):
            units_for_char = 2 if ord(text[end]) > 0xFFFF else 1
            if units + units_for_char > _TELEGRAM_PART_BODY_UNITS:
                break
            units += units_for_char
            end += 1
        if end == start:
            return None

        if end < len(text):
            newline = text.rfind("\n", start + 1, end + 1)
            # Prefer a whole line, but do not create tiny parts on unusual input.
            if newline >= start + min(100, (end - start) // 3):
                end = newline + 1
        parts.append(text[start:end])
        start = end
    return parts


class TelegramBotError(RuntimeError):
    """A sanitized Telegram transport or protocol failure."""


class ApiRequestError(RuntimeError):
    """A sanitized error while calling the private Emergenzix API."""


# Retain the original import path for callers and existing Telegram tests.
from .telegram_backtest_format import (
    _finite_number,
    _one_line,
    _percent,
    format_backtest_record,
)


def _valid_record_id(value: object) -> bool:
    """Accept only canonical SHA-256 record IDs, never path-like user input."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def format_saved_comparison(comparison: object, record_ids: list[str]) -> str:
    """Present two independent saved observations without ranking strategies."""
    invalid = "历史比较响应格式异常，请检查 API；未运行策略或创建订单。"
    if not isinstance(comparison, dict) or len(record_ids) != 2:
        return invalid
    if (
        comparison.get("comparison_type") != "independent_saved_observations"
        or comparison.get("controlled_head_to_head") is not False
        or comparison.get("automatic_winner_selection") is not False
        or not isinstance(comparison.get("different_test_periods"), bool)
        or not isinstance(comparison.get("different_cost_assumptions"), bool)
    ):
        return invalid
    observations = comparison.get("observations")
    if not isinstance(observations, list) or len(observations) != 2:
        return invalid

    lines = ["Emergenzix · 已保存回测比较（独立样本）"]
    dataset_ids: list[str] = []
    for index, (record, expected_id) in enumerate(zip(observations, record_ids), start=1):
        if not isinstance(record, dict) or record.get("record_id") != expected_id:
            return invalid
        selected = record.get("selected_candidate")
        strategy = record.get("strategy_type")
        start = record.get("test_start_at")
        end = record.get("test_end_at_exclusive")
        dataset_id = record.get("dataset_sha256")
        trades = record.get("number_of_trades")
        if (
            not all(isinstance(value, str) and value.strip()
                    for value in (selected, strategy, start, end))
            or not _valid_record_id(dataset_id)
            or isinstance(trades, bool)
            or not isinstance(trades, int)
            or trades < 0
        ):
            return invalid
        numeric_keys = (
            "net_return_pct",
            "benchmark_return_pct",
            "max_drawdown_pct",
            "win_rate_pct",
            "total_fees",
            "fee_bps_per_side",
            "slippage_bps_per_side",
        )
        numbers = {key: _finite_number(record.get(key)) for key in numeric_keys}
        if any(value is None for value in numbers.values()):
            return invalid
        dataset_ids.append(dataset_id)
        lines.extend([
            "",
            f"【{index}】{_one_line(selected, 50)} · {_one_line(strategy, 40)}",
            f"测试期（UTC）：{_one_line(start, 35)} 至 {_one_line(end, 35)}（不含结束）",
            f"净收益：{_percent(numbers['net_return_pct'], signed=True)}；"
            f"基准：{_percent(numbers['benchmark_return_pct'], signed=True)}",
            f"最大回撤：{_percent(numbers['max_drawdown_pct'])}；"
            f"交易次数：{trades}；胜率：{_percent(numbers['win_rate_pct'])}",
            f"单边手续费/滑点：{numbers['fee_bps_per_side']:g}/"
            f"{numbers['slippage_bps_per_side']:g} bps；总手续费：{numbers['total_fees']:,.2f}",
            f"记录：{expected_id[:16]}…",
        ])

    lines.append("")
    if comparison["different_test_periods"]:
        lines.append("⚠ 测试区间不同，收益率不能直接比较优劣。")
    if comparison["different_cost_assumptions"]:
        lines.append("⚠ 手续费或滑点假设不同，结果不具有可比性。")
    if len(set(dataset_ids)) > 1:
        lines.append("⚠ 数据集不同，不属于同一数据条件下的对照。")
    lines.append("仅为两次独立历史研究观察，不是受控策略对照；不自动选优或创建订单。")
    return "\n".join(lines)


_EVIDENCE_CHECK_LABELS = {
    "pretest_trade_sample": "训练期候选交易样本（至少 2 笔）",
    "holdout_trade_sample": "测试期交易样本（至少 2 笔）",
    "positive_holdout_return": "测试期净收益为正",
    "walk_forward_trade_coverage": "Walk-forward 至少 2 折有交易证据",
    "walk_forward_positive_folds": "Walk-forward 至少 2 折正收益",
    "walk_forward_candidate_consistency": "三折选出的候选与最终候选一致",
    "cost_stress_positive_grid": "九种成本情景均为正且有最低交易样本",
}


def _strict_evidence_number(value: object) -> float | None:
    """Avoid treating strings, booleans, and non-finite API data as real metrics."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = _finite_number(value)
    return number


def format_saved_evidence(assessment: object, record_id: str) -> str:
    """Render only a complete, non-authorizing read-only evidence assessment."""
    invalid = "证据评估响应格式异常，未显示不可信结果；没有运行策略或创建订单。"
    if not _valid_record_id(record_id) or not isinstance(assessment, dict):
        return invalid
    if (
        assessment.get("record_id") != record_id
        or not _valid_record_id(assessment.get("record_sha256"))
        or assessment.get("assessment_version") != "date-range-evidence-v1"
        or assessment.get("protocol_version") != "btc-date-range-backtest-v4"
        or assessment.get("automatic_strategy_promotion") is not False
        or assessment.get("paper_order_permission") is not False
        or assessment.get("testnet_order_permission") is not False
        or assessment.get("live_order_permission") is not False
    ):
        return invalid

    checks = assessment.get("screening_checks")
    observations = assessment.get("observations")
    if (
        not isinstance(checks, dict)
        or set(checks) != set(_EVIDENCE_CHECK_LABELS)
        or any(type(value) is not bool for value in checks.values())
        or not isinstance(observations, dict)
    ):
        return invalid

    measured_counts = (
        "validation_completed_trades",
        "holdout_completed_trades",
        "walk_forward_folds_with_trade_evidence",
        "walk_forward_positive_folds",
        "stress_scenarios_positive",
        "stress_scenarios_total",
    )
    if any(
        type(observations.get(name)) is not int or observations[name] < 0
        for name in measured_counts
    ):
        return invalid
    if (
        observations["walk_forward_folds_with_trade_evidence"] > 3
        or observations["walk_forward_positive_folds"] > 3
        or observations["stress_scenarios_total"] != 9
        or observations["stress_scenarios_positive"] > 9
        or type(observations.get("walk_forward_same_selected_candidate")) is not bool
    ):
        return invalid

    holdout_return = _strict_evidence_number(observations.get("holdout_net_return_pct"))
    worst_stress = _strict_evidence_number(observations.get("worst_stress_net_return_pct"))
    if holdout_return is None or worst_stress is None:
        return invalid
    if (
        checks["pretest_trade_sample"] != (observations["validation_completed_trades"] >= 2)
        or checks["holdout_trade_sample"] != (observations["holdout_completed_trades"] >= 2)
        or checks["positive_holdout_return"] != (holdout_return > 0)
        or checks["walk_forward_trade_coverage"]
        != (observations["walk_forward_folds_with_trade_evidence"] >= 2)
        or checks["walk_forward_positive_folds"]
        != (observations["walk_forward_positive_folds"] >= 2)
        or checks["walk_forward_candidate_consistency"]
        != observations["walk_forward_same_selected_candidate"]
        or (checks["cost_stress_positive_grid"]
            and observations["stress_scenarios_positive"] != 9)
    ):
        return invalid

    status = assessment.get("screening_status")
    all_met = all(checks.values())
    if status != ("checks_met" if all_met else "limited_evidence"):
        return invalid

    title = "筛查条件全部满足（不代表统计显著）" if all_met else "证据有限（不能判定策略可靠）"
    lines = [
        "Emergenzix · 历史策略证据评估",
        f"记录：{record_id}",
        f"评估：{title}",
        f"测试期净收益率：{_percent(holdout_return, signed=True)}",
        f"训练期/测试期已平仓交易数："
        f"{observations['validation_completed_trades']}/"
        f"{observations['holdout_completed_trades']}",
        f"Walk-forward：{observations['walk_forward_folds_with_trade_evidence']}/3 折有最低交易证据；"
        f"{observations['walk_forward_positive_folds']}/3 折正收益",
        f"交易成本压力测试：{observations['stress_scenarios_positive']}/9 情景正收益；"
        f"最差净收益率 {_percent(worst_stress, signed=True)}",
        "",
        "七项证据筛查（启发式，不是统计检验）：",
    ]
    lines.extend(
        ("✓ " if checks[key] else "✗ ") + label
        for key, label in _EVIDENCE_CHECK_LABELS.items()
    )
    lines.extend([
        "",
        "注意：最低 2 笔交易不足以证明统计显著性；历史正收益并不代表未来获利。",
        "本评估不会晋级策略，也不授权 PAPER、TESTNET 或 LIVE 下单。",
    ])
    return "\n".join(lines)



def _stats_distribution(value: object, measurements: list[float]) -> dict[str, float] | None:
    """Validate all reported distribution values against source observations."""
    if not measurements:
        if value is not None:
            raise ValueError("unexpected empty-sample distribution")
        return None
    if not isinstance(value, dict) or set(value) != {"mean", "median", "minimum", "maximum"}:
        raise ValueError("invalid statistics distribution")
    from statistics import fmean, median

    expected = {
        "mean": round(fmean(measurements), 8),
        "median": round(median(measurements), 8),
        "minimum": round(min(measurements), 8),
        "maximum": round(max(measurements), 8),
    }
    for name, expected_value in expected.items():
        result = _strict_evidence_number(value.get(name))
        if result is None or abs(result - expected_value) > 0.000001:
            raise ValueError("inconsistent statistics distribution")
    return expected


def _stats_day(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("invalid test window timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid test window timestamp") from exc
    if (
        parsed.utcoffset() != timedelta(0)
        or parsed.time() != datetime_time(0, 0)
    ):
        raise ValueError("invalid test window alignment")
    return parsed


def format_saved_period_statistics(statistics: object, requested_limit: int) -> str:
    """Fail closed on broken/misleading sample metadata; show no portfolio PnL."""
    invalid = "历史统计响应格式异常，未展示不可信的汇总数据；没有执行交易。"
    if not isinstance(statistics, dict) or type(requested_limit) is not int:
        return invalid
    if not 2 <= requested_limit <= 100:
        return invalid
    if (
        statistics.get("statistics_version") != "date-range-statistics-v1"
        or statistics.get("protocol_version") != "btc-date-range-backtest-v4"
        or statistics.get("aggregation_type") != "descriptive_saved_observations"
        or any(
            statistics.get(key) is not False
            for key in (
                "automatic_winner_selection", "strategy_promotion_allowed",
                "paper_order_permission", "testnet_order_permission",
                "live_order_permission",
            )
        )
    ):
        return invalid

    selection = statistics.get("selection")
    records = statistics.get("observations")
    sample = statistics.get("sample_size")
    ids = selection.get("record_ids") if isinstance(selection, dict) else None
    if (
        not isinstance(selection, dict)
        or selection.get("method") != "latest_by_created_at"
        or type(selection.get("limit")) is not int
        or selection["limit"] != requested_limit
        or type(sample) is not int
        or not 0 <= sample <= requested_limit
        or not isinstance(records, list)
        or not isinstance(ids, list)
        or len(records) != sample
        or len(ids) != sample
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != sample
    ):
        return invalid

    flags = (
        "different_cost_assumptions", "different_selected_candidates",
        "all_test_windows_seven_days", "nonoverlapping_seven_day_windows",
    )
    if any(type(statistics.get(key)) is not bool for key in flags):
        return invalid
    integers = (
        "profitable_observation_count", "losing_observation_count",
        "flat_observation_count", "distinct_test_windows",
        "overlapping_test_window_pairs", "repeated_test_window_count",
    )
    if any(
        type(statistics.get(key)) is not int or statistics[key] < 0
        for key in integers
    ):
        return invalid
    if statistics.get("status") != (
        "descriptive_only" if sample >= 2 else "insufficient_records"
    ):
        return invalid

    returns: list[float] = []
    drawdowns: list[float] = []
    windows: list[tuple[datetime, datetime]] = []
    costs: set[tuple[float, float]] = set()
    candidates: set[str] = set()
    try:
        for index, row in enumerate(records):
            if (
                not isinstance(row, dict)
                or not _valid_record_id(ids[index])
                or row.get("record_id") != ids[index]
                or row.get("protocol_version") != "btc-date-range-backtest-v4"
                or not _valid_record_id(row.get("dataset_sha256"))
                or not isinstance(row.get("selected_candidate"), str)
                or not row["selected_candidate"]
            ):
                raise ValueError("invalid source observation")
            ret = _strict_evidence_number(row.get("net_return_pct"))
            dd = _strict_evidence_number(row.get("max_drawdown_pct"))
            fee = _strict_evidence_number(row.get("fee_bps_per_side"))
            slip = _strict_evidence_number(row.get("slippage_bps_per_side"))
            if (
                ret is None or ret < -100 or dd is None or not 0 <= dd <= 100
                or fee is None or fee < 0 or slip is None or slip < 0
            ):
                raise ValueError("invalid source metrics")
            start = _stats_day(row.get("test_start_at"))
            end = _stats_day(row.get("test_end_at_exclusive"))
            duration = end - start
            if not timedelta(days=1) <= duration <= timedelta(days=30):
                raise ValueError("invalid research period")
            returns.append(ret)
            drawdowns.append(dd)
            windows.append((start, end))
            costs.add((fee, slip))
            candidates.add(row["selected_candidate"])

        _stats_distribution(statistics.get("return_distribution_pct"), returns)
        _stats_distribution(
            statistics.get("individual_max_drawdown_distribution_pct"), drawdowns
        )
    except (ValueError, OverflowError, TypeError):
        return invalid

    overlap_pairs = sum(
        start_a < end_b and start_b < end_a
        for i, (start_a, end_a) in enumerate(windows)
        for start_b, end_b in windows[i + 1:]
    )
    distinct_windows = len(set(windows))
    seven_day = all(end - start == timedelta(days=7) for start, end in windows)
    disjoint_seven_day = sample >= 2 and seven_day and overlap_pairs == 0
    if (
        statistics["profitable_observation_count"] != sum(value > 0 for value in returns)
        or statistics["losing_observation_count"] != sum(value < 0 for value in returns)
        or statistics["flat_observation_count"] != sum(value == 0 for value in returns)
        or statistics["distinct_test_windows"] != distinct_windows
        or statistics["overlapping_test_window_pairs"] != overlap_pairs
        or statistics["repeated_test_window_count"] != sample - distinct_windows
        or statistics["different_cost_assumptions"] != (len(costs) > 1)
        or statistics["different_selected_candidates"] != (len(candidates) > 1)
        or statistics["all_test_windows_seven_days"] != seven_day
        or statistics["nonoverlapping_seven_day_windows"] != disjoint_seven_day
    ):
        return invalid

    lines = [
        "Emergenzix · 已保存历史回测跨周期统计",
        f"统计范围：最近 {requested_limit} 条；实际 {sample} 条已保存记录",
    ]
    if not sample:
        lines.append("暂无已保存的回测记录，无法计算收益或回撤分布。")
    else:
        ret_dist = _stats_distribution(statistics["return_distribution_pct"], returns)
        dd_dist = _stats_distribution(
            statistics["individual_max_drawdown_distribution_pct"], drawdowns
        )
        lines.extend([
            f"正收益 / 负收益 / 持平记录："
            f"{statistics['profitable_observation_count']} / "
            f"{statistics['losing_observation_count']} / "
            f"{statistics['flat_observation_count']}",
            f"单次回测净收益：均值 {_percent(ret_dist['mean'], signed=True)}；"
            f"中位数 {_percent(ret_dist['median'], signed=True)}",
            f"收益区间：{_percent(ret_dist['minimum'], signed=True)} ~ "
            f"{_percent(ret_dist['maximum'], signed=True)}",
            f"单次最大回撤：均值 {_percent(dd_dist['mean'])}；"
            f"中位数 {_percent(dd_dist['median'])}；"
            f"区间 {_percent(dd_dist['minimum'])} ~ {_percent(dd_dist['maximum'])}",
        ])

    if sample < 2:
        lines.append("⚠ 保存记录不足两条，无法讨论跨周期稳定性。")
    if statistics["repeated_test_window_count"]:
        lines.append(f"⚠ 相同测试区间重复：{statistics['repeated_test_window_count']} 条额外记录。")
    if overlap_pairs:
        lines.append(f"⚠ 存在 {overlap_pairs} 对重叠测试区间；不能当作独立交易周。")
    if statistics["different_cost_assumptions"]:
        lines.append("⚠ 手续费或滑点设定不一致。")
    if statistics["different_selected_candidates"]:
        lines.append("⚠ 不同日期选择了不同候选策略，并非固定策略的业绩记录。")
    if not seven_day and sample:
        lines.append("⚠ 并非所有记录都覆盖七天，盈利记录数不等于盈利周数。")
    if disjoint_seven_day:
        lines.append("测试区间：每条均为七天，且互不重叠；训练数据和市场环境仍可能相关。")

    lines.extend([
        "",
        "提示：以上仅为已保存实验的描述性统计，不是实盘资金曲线；"
        "不能复利相乘，也不能推断投资组合回撤或未来收益。",
        "没有自动选优、晋级策略或授权 PAPER / TESTNET / LIVE 下单。",
    ])
    return "\n".join(lines)


class TelegramCommandBot:
    """Poll Telegram updates and expose a small, allowlisted research command set."""

    def __init__(
        self,
        *,
        bot_token: str,
        allowed_chat_id: str,
        api_base_url: str = "http://127.0.0.1:8000",
        state_path: str | Path = "data/telegram-bot-state.json",
        poll_timeout_seconds: int = 25,
        backtest_cooldown_seconds: int = 60,
        batch_commands_enabled: bool = False,
        batch_db_path: str | Path | None = None,
        requester: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token is required")
        if not allowed_chat_id.strip():
            raise ValueError("an explicit Telegram allowed chat ID is required")
        parsed_url = urllib.parse.urlparse(api_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("API base URL must be an absolute HTTP(S) URL")
        if not 1 <= poll_timeout_seconds <= 50:
            raise ValueError("Telegram poll timeout must be between 1 and 50 seconds")
        if backtest_cooldown_seconds < 0:
            raise ValueError("backtest cooldown must not be negative")
        if not isinstance(batch_commands_enabled, bool):
            raise ValueError("batch enable flag must be boolean")
        if batch_commands_enabled and (batch_db_path is None or not str(batch_db_path).strip()):
            raise ValueError("batch commands require an explicit shared SQLite path")

        self.bot_token = bot_token.strip()
        self.allowed_chat_id = allowed_chat_id.strip()
        self.api_base_url = api_base_url.rstrip("/")
        self.state_path = Path(state_path).expanduser()
        self.poll_timeout_seconds = poll_timeout_seconds
        self.backtest_cooldown_seconds = backtest_cooldown_seconds
        self.batch_commands_enabled = batch_commands_enabled
        self.batch_db_path = Path(batch_db_path).expanduser() if batch_db_path is not None else None
        self.requester = requester
        self.clock = clock
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"last_update_id": None, "last_backtest_at": None, "poll_initialized": False}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Telegram bot state file cannot be read") from exc
        if not isinstance(payload, dict):
            raise ValueError("Telegram bot state file has an invalid structure")

        update_id = payload.get("last_update_id")
        if update_id is not None and (
            isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0
        ):
            raise ValueError("Telegram bot state file has an invalid update ID")
        last_backtest_at = payload.get("last_backtest_at")
        if last_backtest_at is not None and (
            isinstance(last_backtest_at, bool)
            or not isinstance(last_backtest_at, (int, float))
            or not math.isfinite(float(last_backtest_at))
            or float(last_backtest_at) < 0
        ):
            raise ValueError("Telegram bot state file has an invalid cooldown timestamp")
        poll_initialized = payload.get("poll_initialized", False)
        if not isinstance(poll_initialized, bool):
            raise ValueError("Telegram bot state file has an invalid initialization flag")
        return {
            "last_update_id": update_id,
            "last_backtest_at": last_backtest_at,
            "poll_initialized": poll_initialized,
        }

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _telegram_call(
        self, method: str, payload: dict[str, Any], *, timeout: float
    ) -> Any:
        endpoint = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        if method == "getUpdates":
            query = {
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for key, value in payload.items()
            }
            request = urllib.request.Request(
                f"{endpoint}?{urllib.parse.urlencode(query)}",
                headers={"Accept": "application/json"},
                method="GET",
            )
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

        try:
            response = self.requester(request, timeout=timeout)
            result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, TimeoutError, UnicodeDecodeError, ValueError) as exc:
            # Never include exception text: Telegram transport errors may contain the token URL.
            raise TelegramBotError(f"Telegram {method} request failed") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramBotError(f"Telegram {method} request failed")
        return result.get("result")

    def _api_request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.api_base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = self.requester(request, timeout=180 if method == "POST" else 10)
            return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                error_payload = {}
            detail = error_payload.get("detail") if isinstance(error_payload, dict) else None
            if not isinstance(detail, str) or not detail.strip():
                detail = "API request rejected"
            detail = _one_line(detail, 220).replace(self.bot_token, "[redacted]")
            raise ApiRequestError(detail or "API request rejected") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ApiRequestError("API 暂时不可用，请稍后重试。") from exc
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiRequestError("API 返回了无法识别的响应。") from exc

    def _send_message(self, chat_id: str, text: str) -> None:
        """Deliver complete bounded reports without silently dropping trailing warnings."""
        parts = _split_telegram_text(text)
        if parts is None:
            parts = [
                "报告超出 Telegram 安全分段发送上限，未发送不完整的结果。"
                "请通过私有 API 或只读 CLI 查看完整记录；仅 PAPER 研究，不创建订单。"
            ]
        elif len(parts) > 1:
            total = len(parts)
            parts = [
                f"【报告 {index}/{total}】\n{part}\n"
                "【仅历史 PAPER 研究，不创建交易订单】"
                for index, part in enumerate(parts, start=1)
            ]
        for part in parts:
            self._telegram_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": part,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )

    @staticmethod
    def _help_text() -> str:
        return (
            "Emergenzix · Telegram 命令\n"
            "/status — 查看 API、PAPER 模式与通知队列\n"
            "/latest — 查看最近一次日期区间回测\n"
            "/history — 查看最近 5 条已保存回测及完整记录 ID\n"
            "/report RECORD_ID — 按完整记录 ID 查看历史报告\n"
            "/compare ID1 ID2 — 对比两条已保存的历史回测\n"
            "/evidence RECORD_ID — 查看历史回测的七项证据筛查\n"
            "/stats [limit] — 汇总最近 2–100 条已保存回测（默认 30）\n"
            "/backtest YYYY-MM-DD [days] — 回测历史区间，days 默认为 7，范围 1–30 天\n"
            "/batch — 批次研究计划/登记/进度/报告（需服务器显式启用）\n"
            "/help — 查看此说明\n\n"
            "回测固定使用 90 天训练窗口、10 bps 单边手续费和 5 bps 单边滑点。"
            "仅运行历史 PAPER 模拟，不会创建订单。"
        )

    def _status_text(self) -> str:
        try:
            health = self._api_request("GET", "/health")
        except ApiRequestError:
            return "无法读取 Emergenzix API 状态。请检查 API 服务；当前未执行任何交易操作。"

        if not isinstance(health, dict):
            return "API 状态响应格式异常；当前未执行任何交易操作。"
        try:
            ready = self._api_request("GET", "/ready")
            readiness = "ready" if isinstance(ready, dict) and ready.get("status") == "ready" else "not ready"
        except ApiRequestError:
            readiness = "not ready / unavailable"

        try:
            notifications = self._api_request("GET", "/api/v1/notifications/status")
            metrics = notifications.get("metrics", {}) if isinstance(notifications, dict) else {}
            if not isinstance(metrics, dict):
                metrics = {}
            queue_status = (
                f"{notifications.get('status', 'unknown')} "
                f"(pending {metrics.get('pending', 'N/A')}, dead {metrics.get('dead', 'N/A')})"
                if isinstance(notifications, dict)
                else "unavailable"
            )
        except ApiRequestError:
            queue_status = "unavailable"

        live_state = (
            "disabled"
            if health.get("live_trading_enabled") is False
            else "not confirmed; commands remain research-only"
        )
        return "\n".join(
            [
                "Emergenzix · 系统状态",
                f"API：{health.get('status', 'unknown')}",
                f"Readiness：{readiness}",
                f"模式：{health.get('mode', 'unknown')}",
                f"LIVE 实盘交易：{live_state}",
                f"通知队列：{queue_status}",
            ]
        )

    def _latest_text(self) -> str:
        try:
            records = self._api_request(
                "GET", "/api/v1/research/date-range-backtests?limit=1"
            )
        except ApiRequestError as exc:
            return f"查询最近回测失败：{_one_line(exc)}"
        if not isinstance(records, list):
            return "回测列表响应格式异常，请检查 API。"
        if not records:
            return "尚无已保存的日期区间回测。发送 /help 查看如何启动一次历史回测。"
        return format_backtest_record(records[0])

    def _history_text(self) -> str:
        """List saved observations without running a strategy or placing orders."""
        try:
            records = self._api_request(
                "GET", "/api/v1/research/date-range-backtests?limit=5"
            )
        except ApiRequestError as exc:
            return f"查询历史回测失败：{_one_line(exc)}"
        if not isinstance(records, list) or len(records) > 5:
            return "回测历史响应格式异常，请检查 API。"
        if not records:
            return "尚无已保存的日期区间回测。发送 /help 查看回测命令。"

        lines = ["Emergenzix · 最近 5 条历史回测（新到旧）"]
        for record in records:
            if not isinstance(record, dict) or not _valid_record_id(record.get("id")):
                return "回测历史响应格式异常，请检查 API。"
            report = record.get("report")
            if not isinstance(report, dict):
                return "回测历史响应格式异常，请检查 API。"
            period = report.get("period")
            selection = report.get("strategy_selection")
            metrics = report.get("test_result")
            if not all(isinstance(value, dict) for value in (period, selection, metrics)):
                return "回测历史响应格式异常，请检查 API。"
            selected = selection.get("selected_candidate")
            start_at = period.get("test_start_at")
            candidate_id = selected.get("candidate_id") if isinstance(selected, dict) else None
            net_return = _finite_number(metrics.get("total_return_pct"))
            if (
                not isinstance(start_at, str)
                or len(start_at) < 10
                or not isinstance(candidate_id, str)
                or not candidate_id.strip()
                or net_return is None
            ):
                return "回测历史响应格式异常，请检查 API。"
            lines.append(
                f"{_one_line(start_at[:10])} | {_one_line(candidate_id, 48)} | "
                f"{_percent(net_return, signed=True)}"
            )
            lines.append(f"/report {record['id']}")

        lines.append("以上为独立历史研究记录，不代表未来收益；没有创建任何订单。")
        return "\n".join(lines)

    def _report_text(self, arguments: list[str]) -> str:
        usage = "用法：/report RECORD_ID（使用 /history 获取完整的 64 位记录 ID）"
        if len(arguments) != 1 or not _valid_record_id(arguments[0]):
            return usage
        try:
            record = self._api_request(
                "GET", f"/api/v1/research/date-range-backtests/{arguments[0]}"
            )
        except ApiRequestError as exc:
            return f"查询历史报告失败：{_one_line(exc)}"
        return format_backtest_record(record)

    def _compare_text(self, arguments: list[str]) -> str:
        usage = "用法：/compare ID1 ID2（使用 /history 获取两个完整的 64 位记录 ID）"
        if (
            len(arguments) != 2
            or arguments[0] == arguments[1]
            or any(not _valid_record_id(record_id) for record_id in arguments)
        ):
            return usage
        query = urllib.parse.urlencode(
            [("record_ids", record_id) for record_id in arguments]
        )
        try:
            comparison = self._api_request(
                "GET", f"/api/v1/research/date-range-comparisons?{query}"
            )
        except ApiRequestError as exc:
            return f"比较历史回测失败：{_one_line(exc)}\n没有运行策略或创建订单。"
        return format_saved_comparison(comparison, arguments)

    def _stats_text(self, arguments: list[str]) -> str:
        usage = "用法：/stats [limit]（默认 30，范围 2–100；只统计已保存回测）"
        if len(arguments) > 1:
            return usage
        if not arguments:
            limit = 30
        elif (
            not arguments[0].isascii()
            or not arguments[0].isdecimal()
            or len(arguments[0]) > 3
        ):
            return usage
        else:
            limit = int(arguments[0])
            if not 2 <= limit <= 100:
                return usage

        try:
            result = self._api_request(
                "GET", f"/api/v1/research/date-range-statistics?limit={limit}"
            )
        except ApiRequestError:
            return (
                "历史统计查询失败：请检查内部只读统计 API 与已保存记录；"
                "没有运行策略或创建订单。"
            )
        return format_saved_period_statistics(result, limit)

    def _evidence_text(self, arguments: list[str]) -> str:
        usage = "用法：/evidence RECORD_ID（使用 /history 获取完整的 64 位记录 ID）"
        if len(arguments) != 1 or not _valid_record_id(arguments[0]):
            return usage
        try:
            assessment = self._api_request(
                "GET",
                f"/api/v1/research/date-range-backtests/{arguments[0]}/evidence",
            )
        except ApiRequestError:
            # Avoid echoing API paths, untrusted error details, or credentials in chat.
            return (
                "证据评估查询失败：记录可能不存在、版本不受支持，或 API 不可用。"
                "请检查 /history 和内部服务；没有执行任何交易。"
            )
        return format_saved_evidence(assessment, arguments[0])

    def _backtest_text(self, arguments: list[str], *, chat_id: str | None = None) -> str:
        usage = "用法：/backtest YYYY-MM-DD [days]（days 为 1–30，默认 7）"
        if len(arguments) not in {1, 2}:
            return usage
        try:
            start_date = date.fromisoformat(arguments[0])
            if start_date.isoformat() != arguments[0]:
                return usage
            duration_days = int(arguments[1]) if len(arguments) == 2 else 7
        except (TypeError, ValueError):
            return usage
        if not 1 <= duration_days <= 30:
            return usage

        try:
            end_at = datetime.combine(
                start_date, datetime_time.min, tzinfo=timezone.utc
            ) + timedelta(days=duration_days)
        except OverflowError:
            return usage
        if end_at > datetime.now(timezone.utc):
            return "回测区间必须全部位于过去。请检查日期和天数后重试。"

        now = self.clock()
        previous_attempt = self.state.get("last_backtest_at")
        if previous_attempt is not None:
            elapsed = now - float(previous_attempt)
            if elapsed < self.backtest_cooldown_seconds:
                remaining = max(1, math.ceil(self.backtest_cooldown_seconds - elapsed))
                return f"回测冷却中，请在 {remaining} 秒后再提交。"

        # Persist the cooldown before making a potentially slow historical-data request.
        self.state["last_backtest_at"] = now
        self._save_state()
        payload = {
            "start_date": start_date.isoformat(),
            "duration_days": duration_days,
            "training_days": 90,
            "initial_cash": 10_000.0,
            "fee_bps": 10.0,
            "slippage_bps": 5.0,
        }
        if chat_id is not None:
            try:
                self._send_message(
                    chat_id,
                    f"已接受回测请求：{start_date.isoformat()} 起，{duration_days} 天（UTC）。"
                    "正在获取历史数据并筛选策略；仅 PAPER 研究，不会创建订单。",
                )
            except TelegramBotError as exc:
                # A transient progress-notification failure must not expose secrets or crash the poller.
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_backtest_progress_notice_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
        try:
            record = self._api_request(
                "POST", "/api/v1/research/date-range-backtests", payload
            )
        except ApiRequestError as exc:
            return f"历史回测失败：{_one_line(exc)}\n没有创建任何订单。"
        return format_backtest_record(record)

    def _batch_text(self, arguments: list[str]) -> str:
        if not self.batch_commands_enabled or self.batch_db_path is None:
            return "批次研究命令尚未启用；请先由服务器管理员显式启用。"
        from .telegram_batch import handle_batch_message

        return handle_batch_message(arguments, db_path=self.batch_db_path)

    def handle_update(self, update: object) -> None:
        if not isinstance(update, dict):
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        # Group chats are rejected even if a group ID is accidentally configured.
        if chat.get("type") != "private":
            return
        chat_id = str(chat.get("id", ""))
        if not chat_id or chat_id != self.allowed_chat_id:
            # Unauthorized senders are intentionally ignored without a response.
            return

        text = message.get("text")
        if not isinstance(text, str):
            return
        parts = text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return
        command = parts[0].split("@", 1)[0].lower()
        arguments = parts[1:]

        if command in {"/help", "/start"}:
            reply = self._help_text()
        elif command == "/status":
            reply = self._status_text()
        elif command == "/latest":
            reply = self._latest_text()
        elif command == "/history":
            reply = self._history_text() if not arguments else "用法：/history（不需要参数）"
        elif command == "/report":
            reply = self._report_text(arguments)
        elif command == "/compare":
            reply = self._compare_text(arguments)
        elif command == "/evidence":
            reply = self._evidence_text(arguments)
        elif command == "/stats":
            reply = self._stats_text(arguments)
        elif command == "/backtest":
            reply = self._backtest_text(arguments, chat_id=chat_id)
        elif command == "/batch":
            reply = self._batch_text(arguments)
        else:
            reply = "未知命令。发送 /help 查看可用命令。"
        self._send_message(chat_id, reply)

    def poll_once(self) -> None:
        if not self.state.get("poll_initialized", False):
            # Drop updates queued before first startup so an old command cannot trigger work.
            pending = self._telegram_call(
                "getUpdates",
                {"offset": -1, "timeout": 0, "allowed_updates": ["message"]},
                timeout=10,
            )
            if not isinstance(pending, list):
                raise TelegramBotError("Telegram getUpdates returned an invalid response")
            pending_ids = [
                item["update_id"]
                for item in pending
                if isinstance(item, dict)
                and isinstance(item.get("update_id"), int)
                and not isinstance(item.get("update_id"), bool)
                and item["update_id"] >= 0
            ]
            if pending_ids:
                self.state["last_update_id"] = max(pending_ids)
            self.state["poll_initialized"] = True
            self._save_state()
            return

        last_update_id = self.state.get("last_update_id")
        payload: dict[str, Any] = {
            "timeout": self.poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if last_update_id is not None:
            payload["offset"] = int(last_update_id) + 1
        updates = self._telegram_call(
            "getUpdates",
            payload,
            timeout=self.poll_timeout_seconds + 10,
        )
        if not isinstance(updates, list):
            raise TelegramBotError("Telegram getUpdates returned an invalid response")

        for update in updates:
            if not isinstance(update, dict):
                continue
            try:
                self.handle_update(update)
            except Exception as exc:
                # Log only the exception class; URLs and response bodies may contain secrets.
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_command_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            finally:
                update_id = update.get("update_id")
                if isinstance(update_id, int) and not isinstance(update_id, bool) and update_id >= 0:
                    current = self.state.get("last_update_id")
                    self.state["last_update_id"] = (
                        update_id if current is None else max(int(current), update_id)
                    )
                    self._save_state()

    def run_forever(self) -> None:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_started",
                    "mode": "PAPER",
                    "live_trading_enabled": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "event_type": "telegram_poll_failed",
                            "error_type": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                time.sleep(5)


def _telegram_batch_enabled() -> bool:
    value = os.getenv("EMERGENZIX_TELEGRAM_BATCH_ENABLED", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("EMERGENZIX_TELEGRAM_BATCH_ENABLED must be true or false")
    return value == "true"


def main() -> int:
    token = os.getenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "").strip()
    allowed_chat_id = os.getenv("EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not allowed_chat_id:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_not_started",
                    "reason": "missing_required_configuration",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    try:
        bot = TelegramCommandBot(
            bot_token=token,
            allowed_chat_id=allowed_chat_id,
            api_base_url=os.getenv(
                "EMERGENZIX_TELEGRAM_API_BASE_URL", "http://127.0.0.1:8000"
            ),
            state_path=os.getenv(
                "EMERGENZIX_TELEGRAM_BOT_STATE_PATH",
                "data/telegram-bot-state.json",
            ),
            poll_timeout_seconds=int(
                os.getenv("EMERGENZIX_TELEGRAM_POLL_TIMEOUT_SECONDS", "25")
            ),
            backtest_cooldown_seconds=int(
                os.getenv("EMERGENZIX_TELEGRAM_BACKTEST_COOLDOWN_SECONDS", "60")
            ),
            batch_commands_enabled=_telegram_batch_enabled(),
            batch_db_path=os.getenv("EMERGENZIX_DB_PATH"),
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "event_type": "telegram_command_bot_not_started",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    try:
        bot.run_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
