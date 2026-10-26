"""Run the pinned Freqtrade sample in an isolated, offline research container.

This is a deliberately separate upstream-engine smoke test, NOT the production
backtest API or the proposed S-003 capital/tranche algorithm. The only engine
command generated here is `backtesting` and the strategy is pinned to the
upstream SampleStrategy available through the verified Git submodule.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from .quick_backtest import resolve_quick_backtest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_UPSTREAM_COMMIT = "c051e9065c5b677f03817dd1f83cb980af1ed684"
STRATEGY_NAME = "SampleStrategy"


def _parse_utc_date(value: str) -> date:
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("start must be YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise argparse.ArgumentTypeError("start must be YYYY-MM-DD")
    return result


def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    base = root / "ops" / "freqtrade"
    compose = base / "docker-compose.research.yml"
    config = base / "config.research.json"
    sample = root / "vendor" / "freqtrade" / "freqtrade" / "templates" / "sample_strategy.py"
    return compose, config, sample, base / "user_data"


def validate_upstream_environment(
    root: Path, *, git_check: bool = True
) -> tuple[Path, Path]:
    """Fail closed on missing submodule, untrusted trading config or mismatch."""
    compose, config, sample, user_data = _paths(root)
    for required in (compose, config, sample):
        if not required.is_file():
            raise ValueError(
                f"missing {required}; initialize vendor/freqtrade with "
                "'git submodule update --init --recursive'"
            )
    settings = json.loads(config.read_text(encoding="utf-8"))
    if (
        settings.get("dry_run") is not True
        or settings.get("trading_mode") != "spot"
        or settings.get("exchange", {}).get("key")
        or settings.get("exchange", {}).get("secret")
        or settings.get("api_server", {}).get("enabled") is not False
        or settings.get("telegram", {}).get("enabled") is not False
    ):
        raise ValueError("research config must be spot, dry-run, keyless and API-disabled")
    if git_check:
        try:
            result = subprocess.run(
                ["git", "-C", str(root / "vendor" / "freqtrade"), "rev-parse", "HEAD"],
                check=True, text=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("could not verify Freqtrade submodule revision") from exc
        if result.stdout.strip() != PINNED_UPSTREAM_COMMIT:
            raise ValueError("Freqtrade source differs from the reviewed pinned commit")
    return compose, user_data


def build_backtest_command(
    *, root: Path, initial_cash: float, period: str,
    start_date: date | None = None, utc_today: date | None = None,
) -> tuple[list[str], date, date]:
    start, days = resolve_quick_backtest(
        initial_cash=initial_cash, period=period,
        start_date=start_date, utc_today=utc_today,
    )
    end = date.fromordinal(start.toordinal() + days)
    compose, _, _, _ = _paths(root)
    cmd = [
        "docker", "compose", "-f", str(compose), "--profile", "freqtrade-research",
        "run", "--rm", "--no-deps", "freqtrade-backtest",
        "backtesting",
        "--config", "/freqtrade/user_data/config.research.json",
        "--strategy", STRATEGY_NAME,
        "--timeframe", "4h",
        "--pairs", "BTC/USDT",
        "--timerange", f"{start:%Y%m%d}-{end:%Y%m%d}",
        "--dry-run-wallet", str(initial_cash),
        "--data-format-ohlcv", "json",
        "--export", "trades",
    ]
    return cmd, start, end


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated upstream Freqtrade BTC spot baseline (backtesting only)"
    )
    parser.add_argument("--capital", type=float, required=True, help="initial USDT")
    parser.add_argument("--period", required=True, help="1w, 1m, 1y, 3y or Nd")
    parser.add_argument("--start", type=_parse_utc_date, default=None)
    args = parser.parse_args(argv)

    try:
        compose, user_data = validate_upstream_environment(PROJECT_ROOT)
        command, start, end = build_backtest_command(
            root=PROJECT_ROOT, initial_cash=args.capital,
            period=args.period, start_date=args.start,
        )
        # Only user_data is writable; no Freqtrade strategy code is copied.
        (user_data / "data").mkdir(parents=True, exist_ok=True)
        (user_data / "backtest_results").mkdir(parents=True, exist_ok=True)
        print(json.dumps({
            "mode": "offline_upstream_backtest",
            "source_commit": PINNED_UPSTREAM_COMMIT,
            "source_strategy": STRATEGY_NAME,
            "initial_cash": args.capital,
            "start_date_utc": start.isoformat(),
            "end_exclusive_utc": end.isoformat(),
            "compose": str(compose),
            "notice": "Not S-003; requires previously downloaded local 4h data.",
        }, ensure_ascii=False), flush=True)
        completed = subprocess.run(command, check=False, cwd=PROJECT_ROOT)
        return completed.returncode
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(
            json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
