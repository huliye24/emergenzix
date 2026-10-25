"""Allowlisted Telegram weekly research batch messages, never an execution gateway.

Plan and registration only create metadata. Status/report are verified read-only
SQLite snapshots. There are no Telegram paths to claim jobs, execute workers,
trade, promote strategies, or access arbitrary database files.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .database import Database
from .research_batch_plan import (
    WeeklyResearchBatchPlanRequest,
    plan_weekly_research_batch,
)
from .research_batch_report import (
    BatchReportError,
    build_research_batch_report_from_file,
)
from .research_batch_state import BatchStateError, register_research_batch

_USAGE = (
    "批次研究命令（UTC）\n"
    "/batch plan YYYY-MM-DD [更多日期] — 仅预览，不写入\n"
    "/batch register YYYY-MM-DD [更多日期] — 登记待执行任务\n"
    "/batch status BATCH_ID — 查询任务状态\n"
    "/batch report BATCH_ID — 查看已完成周的验证报告\n"
    "每周固定 7 天测试、90 天训练，最多 12 周，日期不可重叠。\n"
    "登记不代表开始运行；本命令不会回测或创建订单。"
)
_BATCH_ID = re.compile(r"[0-9a-f]{64}\Z")
_REPORT_STATES = {
    "pending": "待执行",
    "leased": "执行中",
    "completed": "完成",
    "failed": "失败",
    "cancelled": "取消",
}


def _friendly_error() -> str:
    return "批次操作暂时不可用，或已保存数据校验未通过。未创建任何订单。"


def _format_progress(report: dict[str, Any]) -> list[str]:
    counts = report["state_counts"]
    return [
        f"批次：{report['batch_id']}",
        f"进度：{counts['completed']}/{report['total_planned']} 周（{report['completion_rate_pct']:.1f}%）",
        "状态：" + "，".join(
            f"{_REPORT_STATES[key]} {counts[key]}"
            for key in ("pending", "leased", "completed", "failed", "cancelled")
        ),
    ]


def _status(report: dict[str, Any]) -> str:
    lines = ["Emergenzix · 批次任务状态", *_format_progress(report)]
    for job in report["jobs"]:
        note = _REPORT_STATES[job["state"]]
        if job["error_code"]:
            note += f" [{job['error_code']}]"
        lines.append(f"{job['sequence']:02d}. {job['test_start_at'][:10]} · {note} · 尝试 {job['attempts']}")
    lines.extend([
        "所有周均为独立历史研究；未完成周不是零收益周。",
        "仅 PAPER 历史研究；没有下单权限。",
    ])
    return "\n".join(lines)


def _metric(value: float) -> str:
    return f"{value:+.4f}%"


def _report(report: dict[str, Any]) -> str:
    lines = ["Emergenzix · 批次研究报告", *_format_progress(report)]
    if not report["completed_financial_observations"]:
        lines.append("尚无已完成并通过验证的历史回测记录。")
    else:
        dist = report["individual_net_return_distribution_pct"]
        drawdown = report["individual_max_drawdown_distribution_pct"]
        lines.extend([
            f"已验证：{report['completed_financial_observations']} 周",
            f"盈利 / 亏损 / 持平：{report['positive_completed_observations']}"
            f" / {report['negative_completed_observations']}"
            f" / {report['flat_completed_observations']}",
            f"单周净收益（均值 / 中位）：{_metric(dist['mean'])} / {_metric(dist['median'])}",
            f"单周最大回撤（均值 / 最大）：{drawdown['mean']:.4f}% / {drawdown['maximum']:.4f}%",
        ])
        for job in report["jobs"]:
            result = job["result"]
            if result is None:
                lines.append(f"{job['sequence']:02d}. {job['test_start_at'][:10]} · {_REPORT_STATES[job['state']]}")
            else:
                lines.append(
                    f"{job['sequence']:02d}. {job['test_start_at'][:10]} · "
                    f"{result['selected_candidate']} · "
                    f"净收益 {_metric(result['net_return_pct'])} · "
                    f"回撤 {result['max_drawdown_pct']:.4f}%"
                )
    if report["different_selected_candidates"]:
        lines.append("提示：各周选中的候选策略并不完全相同。")
    lines.extend([
        f"校验快照：{report['snapshot_sha256'][:16]}…",
        "这些是独立样本外历史模拟；不能累乘为可投资的组合收益。",
        "未完成周不参与收益统计；没有下单或实盘授权。",
    ])
    return "\n".join(lines)


def handle_batch_message(
    arguments: list[str],
    *,
    db_path: str | Path,
) -> str:
    """Execute only approved subcommands against the operator-selected DB path."""
    if not arguments:
        return _USAGE
    subcommand = arguments[0].lower()
    if subcommand in {"run", "execute", "trade", "live", "retry", "cancel"}:
        return "Telegram 不支持执行或交易控制。\n" + _USAGE
    if subcommand in {"plan", "register"}:
        dates = arguments[1:]
        if not 1 <= len(dates) <= 12:
            return _USAGE
        try:
            request = WeeklyResearchBatchPlanRequest.model_validate({
                "start_dates": dates,
            })
            plan = plan_weekly_research_batch(request)
        except (ValidationError, ValueError, TypeError, OverflowError):
            return "历史日期无效、重叠或尚未结束。请使用 UTC YYYY-MM-DD。\n" + _USAGE
        if subcommand == "plan":
            lines = [
                "Emergenzix · 历史研究计划（尚未登记）",
                f"批次 ID：{plan['batch_id']}",
                f"计划周数：{plan['periods_count']}",
            ]
            lines.extend(
                f"{period['sequence']:02d}. {period['backtest_request']['start_date']}"
                for period in plan["periods"]
            )
            lines.extend([
                "执行 /batch register 加同样日期，仅登记任务，不运行回测。",
                "固定 90 天训练、7 天测试、10bps 手续费与 5bps 滑点；不会下单。",
            ])
            return "\n".join(lines)
        # Do not allow typos in Compose database paths to create a second,
        # empty ledger. API initialization must have created the shared file.
        path = Path(db_path).expanduser()
        if not path.is_file():
            return "批次数据库尚未就绪，请检查 API 和共享数据卷。未登记任务。"
        try:
            db = Database(str(path))
            state = register_research_batch(db, plan)
        except (BatchStateError, sqlite3.Error, OSError, ValueError, TypeError):
            return _friendly_error()
        return "\n".join([
            "Emergenzix · 批次登记成功（或已存在）",
            f"批次 ID：{state['batch_id']}",
            f"计划周数：{state['job_count']}",
            "任务登记不会启动回测。后续由受控的云端 Worker 逐周执行。",
            "可用 /batch status BATCH_ID 查看进度；没有创建订单。",
        ])
    if subcommand in {"status", "report"}:
        if len(arguments) != 2 or _BATCH_ID.fullmatch(arguments[1]) is None:
            return _USAGE
        try:
            report = build_research_batch_report_from_file(db_path, arguments[1])
        except (BatchReportError, OSError, ValueError, TypeError, sqlite3.Error):
            return _friendly_error()
        return _status(report) if subcommand == "status" else _report(report)
    return _USAGE
