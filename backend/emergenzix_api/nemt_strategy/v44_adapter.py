"""Research-only monthly NEMT v4.4 router adapter.

The adapter replays the frozen causal router decisions from the validated
research artifact and delegates each month to the corresponding v4 engine.
It intentionally has no PAPER, TESTNET, or LIVE order integration.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator

from .registry import get_strategy, report_fingerprint


def _month_starts(start: date, end: date) -> Iterator[tuple[date, date]]:
    current = start
    while current < end:
        following = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        yield current, min(following, end)
        current = following


def validated_decisions() -> dict[str, dict[str, Any]]:
    """Load causal choices from the content-hashed v4.4 validation artifact."""
    artifact = json.loads(
        files(__package__).joinpath("evidence/v44_monthly_2024_2026.json").read_text("utf-8")
    )
    return {item["month"]: item for item in artifact["walk_forward"]["decisions"]}


def route_for_month(month: date) -> dict[str, Any]:
    """Return a previously validated causal decision or fail closed."""
    key = month.strftime("%Y-%m")
    try:
        decision = validated_decisions()[key]
    except KeyError as exc:
        raise ValueError(f"no reviewed v4.4 router decision for {key}") from exc
    if decision["selected_variant"] not in {"v4a", "v4b", "v4c"}:
        raise ValueError(f"unapproved routed variant for {key}")
    return decision


def run_v44(*, start: date, end: date, capital: float, output_dir: Path) -> dict[str, Any]:
    """Run the routed monthly backtest and return a fingerprinted report."""
    if start.day != 1 or end.day != 1 or end <= start:
        raise ValueError("v4.4 routed ranges must use exclusive month boundaries")
    output_dir.mkdir(parents=True, exist_ok=True)
    strategy = get_strategy()
    initial_capital = capital
    monthly: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    all_curve: list[dict[str, Any]] = []

    for month_start, month_end in _month_starts(start, end):
        decision = route_for_month(month_start)
        command = [
            sys.executable,
            "-m",
            "emergenzix_api.nemt_strategy.engine_cli",
            "--start", month_start.isoformat(),
            "--end", month_end.isoformat(),
            "--capital", str(capital),
            "--variant", decision["selected_variant"],
            "--output-dir", str(output_dir / "months"),
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
        child = json.loads(Path(summary["json_path"]).read_text("utf-8"))
        capital = float(child["metrics"]["final_equity"])
        monthly.append({
            "month": month_start.strftime("%Y-%m"),
            "regime": decision["regime"],
            "selected_variant": decision["selected_variant"],
            "estimated_utility": decision["estimated_utility"],
            "initial_equity": child["metrics"]["initial_equity"],
            "final_equity": child["metrics"]["final_equity"],
            "return_pct": child["metrics"]["total_return_pct"],
            "max_drawdown_pct": child["metrics"]["max_drawdown_pct"],
            "trades": child["metrics"]["trades"],
            "child_report_fingerprint": child["report_fingerprint"],
        })
        all_trades.extend(child["trades"])
        all_curve.extend(child["equity_curve"])

    equities = [initial_capital] + [float(item["equity"]) for item in all_curve]
    peak = equities[0]
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
    wins = [float(item["net_pnl"]) for item in all_trades if item["net_pnl"] > 0]
    losses = [-float(item["net_pnl"]) for item in all_trades if item["net_pnl"] <= 0]
    report: dict[str, Any] = {
        "record_type": "nemt_v44_routed_continuous_backtest",
        "start_local": start.isoformat(),
        "end_local_exclusive": end.isoformat(),
        "provenance": {
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.version,
            "router_protocol": strategy.router_protocol,
            "source_commit": strategy.source_commit,
            "validation_commit": strategy.validation_commit,
            "parameter_fingerprint": strategy.parameter_fingerprint,
            "evidence_fingerprint": strategy.evidence_fingerprint,
            "execution_mode": strategy.execution_mode,
            "live_enabled": strategy.live_enabled,
        },
        "metrics": {
            "initial_equity": initial_capital,
            "final_equity": round(capital, 8),
            "total_return_pct": round((capital / initial_capital - 1) * 100, 6),
            "max_drawdown_pct": round(max_drawdown * 100, 6),
            "trades": len(all_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(all_trades) * 100, 6) if all_trades else 0.0,
            "profit_factor": round(sum(wins) / sum(losses), 6) if losses else None,
            "payoff_ratio": round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 6)
                if wins and losses else None,
        },
        "monthly_routes": monthly,
        "trades": all_trades,
        "equity_curve": all_curve,
        "limitations": [
            "Historical simulation only; no order endpoint is connected.",
            "Funding, liquidation tiers, latency, and order-book depth are not modeled.",
            "Router choices are frozen causal decisions from the reviewed validation artifact.",
        ],
    }
    report["report_fingerprint"] = report_fingerprint(report)
    path = output_dir / f"nemt-v44-{start.isoformat()}-to-{end.isoformat()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"json_path": str(path), "metrics": report["metrics"], "report_fingerprint": report["report_fingerprint"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reviewed NEMT v4.4 research adapter")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports"))
    args = parser.parse_args()
    print(json.dumps(run_v44(
        start=date.fromisoformat(args.start), end=date.fromisoformat(args.end),
        capital=args.capital, output_dir=args.output_dir,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
