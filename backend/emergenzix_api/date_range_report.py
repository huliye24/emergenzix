"""Export date-range backtest records as human-readable and tabular artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_report_artifacts(
    record: dict[str, Any], output_path: str | Path
) -> dict[str, str]:
    """Write a JSON report, human-readable Markdown, and CSV result tables."""
    json_path = Path(output_path).expanduser()
    if json_path.suffix.lower() != ".json":
        raise ValueError("output path must end in .json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    report = record["report"]
    selection = report["strategy_selection"]
    selected = selection["selected_candidate"]
    parameters = selected["parameters"]
    metrics = report["test_result"]
    stem = json_path.stem
    summary_path = json_path.with_name(f"{stem}.summary.csv")
    candidates_path = json_path.with_name(f"{stem}.candidates.csv")
    equity_path = json_path.with_name(f"{stem}.equity.csv")
    trades_path = json_path.with_name(f"{stem}.trades.csv")
    cost_sensitivity_path = json_path.with_name(f"{stem}.cost-sensitivity.csv")
    markdown_path = json_path.with_suffix(".md")

    def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    summary = {
        "record_id": record["id"],
        "record_sha256": record["record_sha256"],
        "symbol": report["market"]["symbol"],
        "timeframe": report["market"]["timeframe"],
        "test_start_at": report["period"]["test_start_at"],
        "test_end_at_exclusive": report["period"]["test_end_at_exclusive"],
        "test_bars_count": report["period"]["test_bars_count"],
        "selected_candidate": selected["candidate_id"],
        "strategy_type": selected["strategy_type"],
        "parameters_json": json.dumps(parameters, sort_keys=True, separators=(",", ":")),
        "initial_cash": metrics["initial_cash"],
        "final_equity": metrics["final_equity"],
        "net_pnl": metrics["net_pnl"],
        "total_return_pct": metrics["total_return_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "benchmark_return_pct": metrics["benchmark_return_pct"],
        "number_of_trades": metrics["number_of_trades"],
        "win_rate_pct": metrics["win_rate_pct"],
        "total_fees": metrics["total_fees"],
        "profit_factor": metrics["profit_factor"],
        "selection_status": selection["status"],
    }
    write_csv(summary_path, list(summary.keys()), [summary])

    candidate_rows = []
    for item in selection["candidates"]:
        candidate_rows.append(
            {
                "candidate_id": item["candidate_id"],
                "strategy_type": item["strategy_type"],
                "parameters_json": json.dumps(
                    item["parameters"], sort_keys=True, separators=(",", ":")
                ),
                "eligible": item["eligible"],
                "selection_score": item["selection_score"],
                "validation_return_pct": item["total_return_pct"],
                "validation_max_drawdown_pct": item["max_drawdown_pct"],
                "validation_trades": item["number_of_trades"],
                "validation_win_rate_pct": item["win_rate_pct"],
                "validation_benchmark_return_pct": item["benchmark_return_pct"],
            }
        )
    write_csv(
        candidates_path,
        [
            "candidate_id",
            "strategy_type",
            "parameters_json",
            "eligible",
            "selection_score",
            "validation_return_pct",
            "validation_max_drawdown_pct",
            "validation_trades",
            "validation_win_rate_pct",
            "validation_benchmark_return_pct",
        ],
        candidate_rows,
    )
    write_csv(equity_path, ["timestamp", "equity"], list(metrics.get("equity_curve", [])))
    write_csv(
        trades_path,
        [
            "entry_timestamp",
            "exit_timestamp",
            "entry_price",
            "exit_price",
            "quantity",
            "entry_fee",
            "exit_fee",
            "net_pnl",
        ],
        list(metrics.get("trades", [])),
    )
    cost_rows = list(report["cost_sensitivity"]["scenarios"])
    write_csv(
        cost_sensitivity_path,
        [
            "candidate_id",
            "strategy_type",
            "parameters",
            "initial_cash",
            "fee_bps_per_side",
            "slippage_bps_per_side",
            "net_return_pct",
            "net_pnl",
            "max_drawdown_pct",
            "number_of_trades",
        ],
        [
            {
                **row,
                "parameters": json.dumps(
                    row["parameters"], sort_keys=True, separators=(",", ":")
                ),
            }
            for row in cost_rows
        ],
    )

    def pct(value: Any) -> str:
        return "N/A" if value is None else f"{float(value):.4f}%"

    candidate_table = [
        "| Candidate | Type | Parameters | Validation return | Max drawdown | Trades | Score |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in selection["candidates"]:
        candidate_table.append(
            "| {candidate} | {strategy_type} | `{parameters}` | {ret} | {drawdown} | {trades} | {score:.4f} |".format(
                candidate=item["candidate_id"],
                strategy_type=item["strategy_type"],
                parameters=json.dumps(
                    item["parameters"], sort_keys=True, separators=(",", ":")
                ),
                ret=pct(item["total_return_pct"]),
                drawdown=pct(item["max_drawdown_pct"]),
                trades=item["number_of_trades"],
                score=float(item["selection_score"]),
            )
        )
    trade_table = [
        "| Entry time | Exit time | Entry price | Exit price | Net PnL |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for trade in metrics["trades"]:
        trade_table.append(
            "| {entry} | {exit} | {entry_price:.4f} | {exit_price:.4f} | {pnl:.4f} |".format(
                entry=trade["entry_timestamp"],
                exit=trade["exit_timestamp"],
                entry_price=float(trade["entry_price"]),
                exit_price=float(trade["exit_price"]),
                pnl=float(trade["net_pnl"]),
            )
        )
    if len(trade_table) == 2:
        trade_table.append("| No completed trades | — | — | — | 0.0000 |")

    cost_table = [
        "| Fee/side | Slippage/side | Net return | Net PnL | Max drawdown | Trades |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in cost_rows:
        cost_table.append(
            "| {fee:.1f} bps | {slippage:.1f} bps | {ret} | {pnl:.4f} | {drawdown} | {trades} |".format(
                fee=float(row["fee_bps_per_side"]),
                slippage=float(row["slippage_bps_per_side"]),
                ret=pct(row["net_return_pct"]),
                pnl=float(row["net_pnl"]),
                drawdown=pct(row["max_drawdown_pct"]),
                trades=int(row["number_of_trades"]),
            )
        )

    profit_factor_display = (
        "N/A"
        if metrics["profit_factor"] is None
        else f"{metrics['profit_factor']:.4f}"
    )
    markdown = "\n".join(
        [
            "# Emergenzix BTC Date-Range Backtest",
            "",
            f"- Record ID: {record['id']}",
            f"- Record SHA-256: {record['record_sha256']}",
            f"- Protocol: {record['protocol_version']}",
            f"- Test period (UTC): {report['period']['test_start_at']} to {report['period']['test_end_at_exclusive']} (end exclusive)",
            f"- Training period: {report['period']['training_days']} days; test candles: {report['period']['test_bars_count']}",
            f"- Selected candidate: {selected['candidate_id']} ({selected['strategy_type']}, parameters={json.dumps(parameters, sort_keys=True, separators=(',', ':'))})",
            f"- Selection status: {selection['status']}",
            "",
            "## Test-week results",
            "",
            "| Metric | Result |",
            "| --- | ---: |",
            f"| Initial cash | {metrics['initial_cash']:.4f} |",
            f"| Final equity | {metrics['final_equity']:.4f} |",
            f"| Net PnL | {metrics['net_pnl']:.4f} |",
            f"| Net return | {pct(metrics['total_return_pct'])} |",
            f"| Buy-and-hold benchmark | {pct(metrics['benchmark_return_pct'])} |",
            f"| Maximum drawdown | {pct(metrics['max_drawdown_pct'])} |",
            f"| Closed trades | {metrics['number_of_trades']} |",
            f"| Win rate | {pct(metrics['win_rate_pct'])} |",
            f"| Total estimated fees | {metrics['total_fees']:.4f} |",
            f"| Profit factor | {profit_factor_display} |",
            "",
            "## Candidate selection (pre-test data only)",
            "",
            *candidate_table,
            "",
            "## Test-week closed trades",
            "",
            *trade_table,
            "",
            "## Transaction-cost sensitivity",
            "",
            "The selected strategy and test window are frozen for every row. This is a stress test, not a profitability guarantee.",
            "",
            *cost_table,
            "",
            "## Interpretation and safety",
            "",
            "- Candidate selection is cut off before the test start; the test week does not select parameters.",
            "- Simulation includes configured fee and slippage assumptions; results are not a promise of future returns.",
            "- This protocol is BTC/USDT spot and long-only. It does not model futures shorts, funding, leverage, liquidation, or hedges.",
            "- No exchange orders were created; live trading remains disabled.",
            "",
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "summary_csv": str(summary_path),
        "candidates_csv": str(candidates_path),
        "equity_csv": str(equity_path),
        "trades_csv": str(trades_path),
        "cost_sensitivity_csv": str(cost_sensitivity_path),
    }
