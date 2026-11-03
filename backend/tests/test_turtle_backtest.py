from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from emergenzix_api.backtesting import run_turtle_breakout
from emergenzix_api.database import Database
from emergenzix_api.main import create_app
from emergenzix_api.schemas import Bar


def _bar(index: int, *, open_price: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc)
        + timedelta(hours=4 * index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def test_turtle_uses_next_open_atr_units_and_channel_exit() -> None:
    bars = [
        _bar(i, open_price=100.0, high=101.0, low=99.0, close=100.0)
        for i in range(30)
    ]
    bars[20] = _bar(20, open_price=100.0, high=103.0, low=99.0, close=102.0)
    bars[21] = _bar(21, open_price=103.0, high=104.0, low=101.0, close=103.0)
    bars[24] = _bar(24, open_price=100.0, high=101.0, low=97.0, close=98.0)
    bars[25] = _bar(25, open_price=97.0, high=98.0, low=96.0, close=97.0)

    result = run_turtle_breakout(
        bars,
        entry_window=20,
        exit_window=10,
        initial_cash=10_000.0,
        fee_bps=10.0,
        slippage_bps=5.0,
    )

    assert result["number_of_trades"] == 1
    trade = result["trades"][0]
    assert trade["entry_timestamp"] == bars[21].timestamp.isoformat()
    assert trade["exit_timestamp"] == bars[25].timestamp.isoformat()
    assert 0 < trade["quantity"] < 100
    assert result["assumptions"]["risk_fraction_per_unit"] == 0.01
    assert result["assumptions"]["positioning"].startswith("long-only")


def test_builtin_turtle_candidates_are_opt_in_and_idempotent(tmp_path) -> None:
    path = tmp_path / "builtins.db"
    Database(str(path), seed_builtin_strategies=True)
    Database(str(path), seed_builtin_strategies=True)

    database = Database(str(path))
    with database.connection() as connection:
        strategies = connection.execute(
            "SELECT symbol, strategy_type, fast_window, slow_window, status "
            "FROM strategies ORDER BY symbol"
        ).fetchall()
        versions = connection.execute(
            "SELECT COUNT(*) FROM strategy_versions"
        ).fetchone()[0]

    assert [row["symbol"] for row in strategies] == ["BNBUSDT", "BTCUSDT", "ETHUSDT"]
    assert all(row["strategy_type"] == "turtle_breakout" for row in strategies)
    assert all((row["fast_window"], row["slow_window"]) == (10, 20) for row in strategies)
    assert all(row["status"] == "candidate" for row in strategies)
    assert versions == 3


@pytest.mark.asyncio
async def test_backtest_api_dispatches_turtle_strategy(tmp_path) -> None:
    app = create_app(
        db_path=tmp_path / "api.db",
        notification_db_path=tmp_path / "notifications.db",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        strategy = await client.post(
            "/api/v1/strategies",
            json={
                "name": "BTC Turtle 20/10",
                "symbol": "BTCUSDT",
                "strategy_type": "turtle_breakout",
                "fast_window": 10,
                "slow_window": 20,
            },
        )
        assert strategy.status_code == 201, strategy.text

        bars = [
            _bar(i, open_price=100.0, high=101.0, low=99.0, close=100.0)
            for i in range(30)
        ]
        bars[20] = _bar(20, open_price=100.0, high=103.0, low=99.0, close=102.0)
        bars[21] = _bar(21, open_price=103.0, high=104.0, low=101.0, close=103.0)

        response = await client.post(
            "/api/v1/backtests",
            json={
                "strategy_id": strategy.json()["id"],
                "bars": [bar.model_dump(mode="json") for bar in bars],
                "initial_cash": 25_000,
                "fee_bps": 10,
                "slippage_bps": 5,
            },
        )

        assert response.status_code == 201, response.text
        result = response.json()
        assert result["strategy"]["strategy_type"] == "turtle_breakout"
        assert result["initial_cash"] == 25_000
        assert result["assumptions"]["signal"].startswith("20-bar breakout")
