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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .quick_backtest import resolve_quick_backtest
from .freqtrade_dataset_bridge import verify_exported_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_UPSTREAM_COMMIT = "c051e9065c5b677f03817dd1f83cb980af1ed684"
STRATEGY_NAME = "SampleStrategy"
PARITY_NAME = "EmergenzixSmaParity"
PARITY_FEE_RATE = "0.001"  # 10 basis points per side
VALID_BASELINES = ("sample", "sma-5-20")


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
    root: Path, *, git_check: bool = True, baseline: str = "sample"
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
    if baseline not in VALID_BASELINES:
        raise ValueError("unsupported upstream baseline")
    if baseline == "sma-5-20":
        parity_code = root / "ops" / "freqtrade" / "strategies" / "EmergenzixSmaParity.py"
        parity_config = root / "ops" / "freqtrade" / "config.parity.json"
        for path in (parity_code, parity_config):
            if not path.is_file():
                raise ValueError(f"missing reviewed SMA parity file: {path}")
        parity_settings = json.loads(parity_config.read_text(encoding="utf-8"))
        if (
            parity_settings.get("dry_run") is not True
            or parity_settings.get("trading_mode") != "spot"
            or parity_settings.get("stake_amount") != "unlimited"
            or parity_settings.get("max_open_trades") != 1
            or parity_settings.get("tradable_balance_ratio") != 1.0
            or parity_settings.get("exchange", {}).get("key")
            or parity_settings.get("exchange", {}).get("secret")
            or parity_settings.get("api_server", {}).get("enabled") is not False
            or parity_settings.get("telegram", {}).get("enabled") is not False
        ):
            raise ValueError("SMA parity config must be keyless, spot and fixed-budget")
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
    baseline: str = "sample",
) -> tuple[list[str], date, date]:
    start, days = resolve_quick_backtest(
        initial_cash=initial_cash, period=period,
        start_date=start_date, utc_today=utc_today,
    )
    end = date.fromordinal(start.toordinal() + days)
    if baseline not in VALID_BASELINES:
        raise ValueError("unsupported upstream baseline")
    compose, _, _, _ = _paths(root)
    strategy = PARITY_NAME if baseline == "sma-5-20" else STRATEGY_NAME
    config_path = (
        "/freqtrade/user_data/config.parity.json"
        if baseline == "sma-5-20" else "/freqtrade/user_data/config.research.json"
    )
    strategy_path = (
        "/freqtrade/user_data/strategies"
        if baseline == "sma-5-20" else "/freqtrade/user_data/upstream_strategies"
    )
    cmd = [
        "docker", "compose", "-f", str(compose), "--profile", "freqtrade-research",
        "run", "--rm", "--no-deps", "freqtrade-backtest",
        "backtesting",
        "--config", config_path,
        "--strategy", strategy,
        "--strategy-path", strategy_path,
        "--timeframe", "4h",
        "--pairs", "BTC/USDT",
        "--timerange", f"{start:%Y%m%d}-{end:%Y%m%d}",
        "--dry-run-wallet", str(initial_cash),
        "--data-format-ohlcv", "json",
        "--export", "trades",
    ]
    if baseline == "sma-5-20":
        cmd.extend(["--fee", PARITY_FEE_RATE])
    return cmd, start, end


def validate_shared_data_window(
    *, root: Path, db_path: Path, dataset_id: str,
    start: date, end: date,
) -> dict:
    """Demand exact source parity and >=200 startup candles before backtesting."""
    data_dir = root / "ops" / "freqtrade" / "user_data" / "data" / "binance"
    manifest = verify_exported_dataset(
        db_path=db_path, dataset_id=dataset_id, data_dir=data_dir,
    )
    first = datetime.fromisoformat(
        manifest["first_open_at_utc"].replace("Z", "+00:00")
    )
    last_close = datetime.fromisoformat(
        manifest["last_close_exclusive_utc"].replace("Z", "+00:00")
    )
    request_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    request_end = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
    required_start = request_start - timedelta(hours=4 * 200)
    if first > required_start or last_close < request_end:
        raise ValueError(
            "shared dataset does not cover full test window plus "
            "Freqtrade's 200 startup 4h candles"
        )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated upstream Freqtrade BTC spot baseline (backtesting only)"
    )
    parser.add_argument("--capital", type=float, required=True, help="initial USDT")
    parser.add_argument("--period", required=True, help="1w, 1m, 1y, 3y or Nd")
    parser.add_argument("--start", type=_parse_utc_date, default=None)
    parser.add_argument(
        "--baseline", choices=VALID_BASELINES, default="sample",
        help="sample: upstream demo; sma-5-20: reviewed crossover signal",
    )
    parser.add_argument(
        "--dataset-id", default=None,
        help="verified Emergenzix source SHA-256; requires --db-path",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="existing content-addressed Emergenzix SQLite data store",
    )
    args = parser.parse_args(argv)

    try:
        if args.baseline == "sma-5-20" and args.dataset_id is None:
            raise ValueError("SMA parity mode requires --dataset-id and --db-path")
        compose, user_data = validate_upstream_environment(
            PROJECT_ROOT, baseline=args.baseline,
        )
        command, start, end = build_backtest_command(
            root=PROJECT_ROOT, initial_cash=args.capital,
            period=args.period, start_date=args.start, baseline=args.baseline,
        )
        if (args.dataset_id is None) != (args.db_path is None):
            raise ValueError("--dataset-id and --db-path must be provided together")
        provenance = None
        if args.dataset_id is not None:
            provenance = validate_shared_data_window(
                root=PROJECT_ROOT, db_path=args.db_path, dataset_id=args.dataset_id,
                start=start, end=end,
            )
        # Only user_data is writable; no Freqtrade strategy code is copied.
        (user_data / "data").mkdir(parents=True, exist_ok=True)
        (user_data / "backtest_results").mkdir(parents=True, exist_ok=True)
        print(json.dumps({
            "mode": "offline_upstream_backtest",
            "source_commit": PINNED_UPSTREAM_COMMIT,
            "source_strategy": PARITY_NAME if args.baseline == "sma-5-20" else STRATEGY_NAME,
            "baseline": args.baseline,
            "fee_per_side": float(PARITY_FEE_RATE) if args.baseline == "sma-5-20" else None,
            "slippage": "Freqtrade native; not the Emergenzix slippage model",
            "initial_cash": args.capital,
            "start_date_utc": start.isoformat(),
            "end_exclusive_utc": end.isoformat(),
            "compose": str(compose),
            "dataset_id": provenance["dataset_id"] if provenance else None,
            "freqtrade_data_sha256": provenance["data_sha256"] if provenance else None,
            "data_parity_verified": provenance is not None,
            "notice": "Signal-parity is not execution/PnL parity. Not NEMT, no live orders.",
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
