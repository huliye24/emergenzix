"""Upstream-engine launch safety without pulling images or contacting exchanges."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from emergenzix_api import freqtrade_research_cli as runner


def test_upstream_command_maps_capital_period_to_offline_backtest() -> None:
    command, start, end = runner.build_backtest_command(
        root=Path("/repo"), initial_cash=100, period="1w",
        start_date=date(2025, 10, 21), utc_today=date(2026, 10, 26),
    )
    assert start == date(2025, 10, 21)
    assert end == date(2025, 10, 28)
    assert command[:3] == [
        "docker", "compose", "-f",
    ]
    assert command[command.index("--no-deps") + 2] == "backtesting"
    assert command[command.index("--timerange") + 1] == "20251021-20251028"
    assert command[command.index("--dry-run-wallet") + 1] == "100"
    assert command[command.index("--strategy") + 1] == "SampleStrategy"
    assert "trade" not in command
    assert "download-data" not in command
    assert "--api-key" not in command


def test_year_duration_matches_quick_backtest_contract() -> None:
    command, start, end = runner.build_backtest_command(
        root=Path("/repo"), initial_cash=120, period="1y",
        start_date=date(2022, 1, 1), utc_today=date(2026, 10, 26),
    )
    assert (end - start).days == 365
    assert command[command.index("--dry-run-wallet") + 1] == "120"
    assert command[command.index("--timerange") + 1] == "20220101-20230101"


def test_reject_future_range_and_unsafe_capital() -> None:
    with pytest.raises(ValueError, match="ended"):
        runner.build_backtest_command(
            root=Path("/repo"), initial_cash=100, period="1w",
            start_date=date(2026, 10, 25), utc_today=date(2026, 10, 26),
        )
    with pytest.raises(ValueError, match="initial_cash"):
        runner.build_backtest_command(
            root=Path("/repo"), initial_cash=float("nan"), period="1w",
            utc_today=date(2026, 10, 26),
        )


def _build_mock_root(tmp_path: Path, *, safe: bool = True) -> Path:
    base = tmp_path / "ops" / "freqtrade"
    base.mkdir(parents=True)
    (base / "docker-compose.research.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (base / "config.research.json").write_text(
        json.dumps({
            "dry_run": safe, "trading_mode": "spot",
            "exchange": {"key": "", "secret": ""},
            "api_server": {"enabled": False},
            "telegram": {"enabled": False},
        }), encoding="utf-8",
    )
    sample = (
        tmp_path / "vendor" / "freqtrade" / "freqtrade"
        / "templates" / "sample_strategy.py"
    )
    sample.parent.mkdir(parents=True)
    sample.write_text("class SampleStrategy: pass\n", encoding="utf-8")
    return tmp_path


def test_safe_config_and_source_checkout_are_required(tmp_path: Path, monkeypatch) -> None:
    root = _build_mock_root(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, runner.PINNED_UPSTREAM_COMMIT + "\n", "",
        ),
    )
    compose, userdata = runner.validate_upstream_environment(root)
    assert compose.is_file()
    assert userdata == root / "ops" / "freqtrade" / "user_data"


def test_refuses_unverified_source_or_live_config(tmp_path: Path, monkeypatch) -> None:
    root = _build_mock_root(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, "other-commit\n", "",
        ),
    )
    with pytest.raises(ValueError, match="differs"):
        runner.validate_upstream_environment(root)

    root = _build_mock_root(tmp_path, safe=False)
    with pytest.raises(ValueError, match="dry-run"):
        runner.validate_upstream_environment(root, git_check=False)


def test_main_only_invokes_research_backtest_and_creates_output_dirs(
    tmp_path: Path, monkeypatch,
) -> None:
    root = _build_mock_root(tmp_path)
    monkeypatch.setattr(runner, "PROJECT_ROOT", root)
    monkeypatch.setattr(
        runner, "validate_upstream_environment",
        lambda target: (
            target / "ops" / "freqtrade" / "docker-compose.research.yml",
            target / "ops" / "freqtrade" / "user_data",
        ),
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main([
        "--capital", "100", "--period", "1w", "--start", "2025-10-21",
    ]) == 0
    assert len(calls) == 1
    assert calls[0][0].count("backtesting") == 1
    assert calls[0][1]["cwd"] == root
    assert (
        root / "ops" / "freqtrade" / "user_data" / "backtest_results"
    ).is_dir()


def test_compose_offline_engine_has_no_ports_or_credentials() -> None:
    root = runner.PROJECT_ROOT
    compose = (
        root / "ops" / "freqtrade" / "docker-compose.research.yml"
    ).read_text(encoding="utf-8")
    assert 'network_mode: "none"' in compose
    assert 'read_only: true' in compose
    assert 'profiles: ["freqtrade-research"]' in compose
    assert 'profiles: ["freqtrade-download"]' in compose
    assert "ports:" not in compose
    assert "trade\n" not in compose
    config = json.loads(
        (root / "ops" / "freqtrade" / "config.research.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["dry_run"] is True
    assert config["trading_mode"] == "spot"
    assert config["exchange"]["key"] == ""
    assert config["exchange"]["secret"] == ""
    assert config["api_server"]["enabled"] is False
    assert config["telegram"]["enabled"] is False
