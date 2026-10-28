"""Offline source-level signal parity: the actual Freqtrade strategy class
is evaluated with pandas, but without Freqtrade's heavy trading dependencies.

This verifies the SMA signal math and next-open *intent*. Matching fills,
wallet balances, fees or final PnL needs a separate end-to-end engine test.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from emergenzix_api.backtesting import run_sma_crossover
from emergenzix_api.freqtrade_research_cli import (
    PARITY_FEE_RATE, PARITY_NAME, build_backtest_command,
    validate_upstream_environment,
)
from emergenzix_api.schemas import Bar


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = ROOT / "ops/freqtrade/strategies/EmergenzixSmaParity.py"


def _strategy_class():
    """Load only the authored IStrategy class node with its pandas dependency."""
    source = ast.parse(STRATEGY_PATH.read_text(encoding="utf-8"))
    cls = next(
        node for node in source.body
        if isinstance(node, ast.ClassDef) and node.name == PARITY_NAME
    )
    namespace = {"IStrategy": object, "DataFrame": pd.DataFrame}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(STRATEGY_PATH), "exec"), namespace)
    return namespace[PARITY_NAME]


def _bars() -> list[Bar]:
    closes = [100.0] * 65
    closes[20] = 103.0  # up crossover -> next open
    closes[25] = 94.0   # down crossover -> next open
    origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=origin + timedelta(hours=4*i),
            open=c, high=c+2, low=c-2, close=c, volume=100,
        )
        for i, c in enumerate(closes)
    ]


def test_sma_signals_match_reference_across_every_completed_bar() -> None:
    bars = _bars()
    strategy = _strategy_class()()
    df = pd.DataFrame({
        "date": [b.timestamp for b in bars],
        "open": [b.open for b in bars],
        "high": [b.high for b in bars],
        "low": [b.low for b in bars],
        "close": [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })
    result = strategy.populate_indicators(df.copy(), {})
    result = strategy.populate_entry_trend(result, {})
    result = strategy.populate_exit_trend(result, {})
    up = set(result.index[result["enter_long"].fillna(0) == 1])
    down = set(result.index[result["exit_long"].fillna(0) == 1])
    expected_up, expected_down = set(), set()
    closes = [b.close for b in bars]
    for t in range(20, len(closes)):
        def sma(n, idx):
            return sum(closes[idx-n+1:idx+1])/n
        prior_fast, prior_slow = sma(5, t-1), sma(20, t-1)
        curr_fast, curr_slow = sma(5, t), sma(20, t)
        if prior_fast <= prior_slow and curr_fast > curr_slow:
            expected_up.add(t)
        if prior_fast >= prior_slow and curr_fast < curr_slow:
            expected_down.add(t)
    assert up == expected_up
    assert down == expected_down
    assert 20 in up
    assert 25 in down

    reference = run_sma_crossover(
        bars, fast_window=5, slow_window=20,
        initial_cash=100, fee_bps=10, slippage_bps=0,
    )
    assert reference["trades"][0]["entry_timestamp"] == bars[21].timestamp.isoformat()
    assert reference["trades"][0]["exit_timestamp"] == bars[26].timestamp.isoformat()
    assert strategy.can_short is False
    assert strategy.position_adjustment_enable is False
    assert strategy.startup_candle_count == 20
    assert strategy.minimal_roi["0"] >= 100
    assert strategy.stoploss < -0.99


def test_sma_contract_never_changes_reference_after_signal_bar() -> None:
    bars = _bars()
    strategy = _strategy_class()()
    df = pd.DataFrame({"close":[b.close for b in bars], "volume":[b.volume for b in bars]})
    first = strategy.populate_indicators(df.copy(), {})
    first = strategy.populate_entry_trend(first, {})
    first = strategy.populate_exit_trend(first, {})
    changed = df.copy()
    changed.loc[30:, "close"] = 200.0
    later = strategy.populate_indicators(changed, {})
    later = strategy.populate_entry_trend(later, {})
    later = strategy.populate_exit_trend(later, {})
    assert first.loc[:29, ["enter_long", "exit_long"]].fillna(0).equals(
        later.loc[:29, ["enter_long", "exit_long"]].fillna(0)
    )


def test_parity_runner_uses_local_strategy_and_explicit_fee() -> None:
    from datetime import date
    command, start, end = build_backtest_command(
        root=ROOT, initial_cash=100, period="1w",
        start_date=date(2025, 10, 21), utc_today=date(2026, 10, 26),
        baseline="sma-5-20",
    )
    assert command[command.index("--strategy") + 1] == PARITY_NAME
    assert command[command.index("--strategy-path") + 1] == "/freqtrade/user_data/strategies"
    assert command[command.index("--config") + 1] == "/freqtrade/user_data/config.parity.json"
    assert command[command.index("--fee") + 1] == PARITY_FEE_RATE == "0.001"
    assert command[command.index("--timerange") + 1] == "20251021-20251028"
    assert "download-data" not in command
    assert "trade" not in command


def test_parity_config_is_keyless_and_does_not_keep_extra_stake() -> None:
    config = json.loads(
        (ROOT/"ops/freqtrade/config.parity.json").read_text(encoding="utf-8")
    )
    assert config["dry_run"] is True
    assert config["trading_mode"] == "spot"
    assert config["tradable_balance_ratio"] == 1.0
    assert config["stake_amount"] == "unlimited"
    assert config["max_open_trades"] == 1
    assert config["exchange"]["key"] == config["exchange"]["secret"] == ""
    assert config["telegram"]["enabled"] is False
    assert config["api_server"]["enabled"] is False


def test_parity_config_checked_before_exec(tmp_path: Path) -> None:
    import shutil
    from emergenzix_api import freqtrade_research_cli as runner
    source = ROOT / "ops/freqtrade"
    dest = tmp_path / "ops/freqtrade"
    dest.mkdir(parents=True)
    for name in ("config.research.json", "config.parity.json",
                 "docker-compose.research.yml"):
        shutil.copyfile(source / name, dest / name)
    (dest/"strategies").mkdir()
    shutil.copyfile(STRATEGY_PATH, dest/"strategies/EmergenzixSmaParity.py")
    upstream = tmp_path/"vendor/freqtrade/freqtrade/templates"
    upstream.mkdir(parents=True)
    (upstream/"sample_strategy.py").write_text("class SampleStrategy: pass\n")
    validate_upstream_environment(tmp_path, git_check=False, baseline="sma-5-20")
    config = json.loads((dest/"config.parity.json").read_text())
    config["tradable_balance_ratio"] = 0.1
    (dest/"config.parity.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="parity config"):
        validate_upstream_environment(tmp_path, git_check=False, baseline="sma-5-20")


def test_parity_missing_verified_dataset_never_runs_docker(monkeypatch, tmp_path: Path) -> None:
    import emergenzix_api.freqtrade_research_cli as runner
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        runner, "validate_upstream_environment",
        lambda *_args, **_kwargs: (
            tmp_path/"ops/freqtrade/docker-compose.research.yml",
            tmp_path/"ops/freqtrade/user_data",
        ),
    )
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *_a, **_kw: pytest.fail("parity must not start Docker without dataset"),
    )
    assert runner.main([
        "--baseline", "sma-5-20",
        "--capital", "100", "--period", "1w", "--start", "2025-10-21",
    ]) == 2


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("stake_currency", "BTC"),
        ("timeframe", "1h"),
        ("dataformat_ohlcv", "feather"),
        ("exchange.name", "kraken"),
        ("exchange.pair_whitelist", ["ETH/USDT"]),
        ("exchange.pair_blacklist", ["BTC/USDT"]),
        ("pairlists", [{"method": "VolumePairList"}]),
    ],
)
def test_parity_config_rejects_market_or_dataset_drift(
    tmp_path: Path, field: str, mutated: object,
) -> None:
    """An apparently harmless config edit must not invalidate the SMA control."""
    import shutil

    source = ROOT / "ops/freqtrade"
    dest = tmp_path / "ops/freqtrade"
    dest.mkdir(parents=True)
    for name in ("config.research.json", "config.parity.json",
                 "docker-compose.research.yml"):
        shutil.copyfile(source / name, dest / name)
    (dest / "strategies").mkdir()
    shutil.copyfile(STRATEGY_PATH, dest / "strategies/EmergenzixSmaParity.py")
    upstream = tmp_path / "vendor/freqtrade/freqtrade/templates"
    upstream.mkdir(parents=True)
    (upstream / "sample_strategy.py").write_text("class SampleStrategy: pass\\n")

    validate_upstream_environment(tmp_path, git_check=False, baseline="sma-5-20")
    config_path = dest / "config.parity.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    target = config
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = mutated
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="parity config"):
        validate_upstream_environment(tmp_path, git_check=False, baseline="sma-5-20")
