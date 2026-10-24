from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.date_range_backtest import run_date_range_backtest
from emergenzix_api.schemas import Bar


def _series(
    *,
    start_at: datetime | None = None,
    training_days: int = 90,
    duration_days: int = 7,
) -> list[Bar]:
    start = start_at or datetime(2025, 10, 21, tzinfo=timezone.utc)
    first = start - timedelta(days=training_days)
    count = (training_days + duration_days) * 6
    bars: list[Bar] = []
    for index in range(count):
        close = 80_000 + 2_500 * math.sin(index * 0.11) + index * 3.0
        open_price = close * (1.0 + 0.0005 * math.sin(index * 0.07))
        bars.append(
            Bar(
                timestamp=first + timedelta(hours=4 * index),
                open=open_price,
                high=max(open_price, close) * 1.003,
                low=min(open_price, close) * 0.997,
                close=close,
                volume=1_000 + index,
            )
        )
    return bars


def _run(bars: list[Bar]) -> dict[str, object]:
    return run_date_range_backtest(
        bars,
        start_at=datetime(2025, 10, 21, tzinfo=timezone.utc),
        duration_days=7,
        training_days=90,
        initial_cash=10_000.0,
        fee_bps=10.0,
        slippage_bps=5.0,
    )


def test_date_range_backtest_selects_only_from_pre_test_data() -> None:
    bars = _series()
    baseline = _run(bars)
    training_count = 90 * 6
    altered = list(bars)
    for index in range(training_count, len(altered)):
        altered[index] = altered[index].model_copy(
            update={
                "open": 50_000.0,
                "high": 50_100.0,
                "low": 49_900.0,
                "close": 50_000.0,
            }
        )

    changed = _run(altered)
    assert changed["strategy_selection"] == baseline["strategy_selection"]
    assert changed["test_result"] != baseline["test_result"]
    assert baseline["safety"] == {
        "mode": "PAPER",
        "orders_created": False,
        "live_trading_enabled": False,
        "future_visible_during_selection": False,
        "test_period_used_for_selection": False,
    }


def test_date_range_backtest_returns_one_week_equity_and_trade_report() -> None:
    report = _run(_series())
    period = report["period"]
    metrics = report["test_result"]

    assert period["test_start_at"] == "2025-10-21T00:00:00Z"
    assert period["test_end_at_exclusive"] == "2025-10-28T00:00:00Z"
    assert period["training_bars_count"] == 540
    assert period["test_bars_count"] == 42
    assert period["test_data_complete"] is True
    assert report["market"]["symbol"] == "BTC/USDT"
    assert report["strategy_selection"]["selected_candidate"]["candidate_id"].startswith("sma-")
    assert report["strategy_selection"]["future_visible_during_selection"] is False
    assert metrics["bars_count"] == 42
    assert len(metrics["equity_curve"]) == 43
    assert isinstance(metrics["trades"], list)
    assert "total_fees" in metrics
    assert "profit_factor" in metrics
    assert report["safety"]["live_trading_enabled"] is False


def test_date_range_backtest_marks_candidate_selection_as_weak_when_no_trades() -> None:
    flat_bars = [
        bar.model_copy(
            update={
                "open": 80_000.0,
                "high": 80_100.0,
                "low": 79_900.0,
                "close": 80_000.0,
            }
        )
        for bar in _series()
    ]
    report = _run(flat_bars)

    assert report["strategy_selection"]["status"] == "insufficient_validation_trades"
    assert report["strategy_selection"]["warning"]
    assert all(
        not candidate["eligible"]
        for candidate in report["strategy_selection"]["candidates"]
    )
    assert report["test_result"]["number_of_trades"] == 0


def test_date_range_backtest_rejects_missing_candles() -> None:
    bars = _series()
    del bars[150]
    with pytest.raises(ValueError, match="incomplete|gap"):
        _run(bars)


def test_date_range_backtest_rejects_naive_and_future_start_dates() -> None:
    bars = _series()
    with pytest.raises(ValueError, match="timezone"):
        run_date_range_backtest(
            bars,
            start_at=datetime(2025, 10, 21),
            duration_days=7,
            training_days=90,
        )

    with pytest.raises(ValueError, match="past"):
        run_date_range_backtest(
            bars,
            start_at=datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
            duration_days=7,
            training_days=90,
        )


def test_date_range_backtest_api_persists_idempotent_report(client, monkeypatch) -> None:
    import emergenzix_api.date_range_backtest as service

    def fake_archive(*, start_at: datetime, end_at: datetime):
        count = int((end_at - start_at).total_seconds() // (4 * 3600)) + 1
        generated = _series(
            start_at=start_at + timedelta(days=90),
            training_days=90,
            duration_days=7,
        )
        # The helper above uses the desired first training candle. Keep one extra
        # candle at end_at to mirror Binance's inclusive archive range behavior.
        extra = generated[-1].model_copy(
            update={
                "timestamp": end_at,
                "open": generated[-1].close,
                "high": generated[-1].close * 1.003,
                "low": generated[-1].close * 0.997,
                "close": generated[-1].close,
            }
        )
        assert count == len(generated) + 1
        return [bar.model_dump(mode="json") for bar in [*generated, extra]]

    monkeypatch.setattr(service, "fetch_binance_btc_4h_archive", fake_archive)
    body = {
        "start_date": "2025-10-21",
        "duration_days": 7,
        "training_days": 90,
        "initial_cash": 10_000.0,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
    }
    first_response = client.post(
        "/api/v1/research/date-range-backtests", json=body
    )
    assert first_response.status_code == 201, first_response.text
    first = first_response.json()
    assert first["record_type"] == "btc_date_range_backtest"
    assert first["dataset"]["source"] == "binance_public_archive"
    assert first["report"]["period"]["test_bars_count"] == 42
    assert first["report"]["data_provenance"]["archive_checksum_verified"] is True
    assert first["report"]["data_provenance"]["coverage_verified"] is True
    assert first["report"]["data_provenance"]["dataset_sha256"] == first["dataset"]["sha256"]
    assert first["safety"]["orders_created"] is False
    assert first["safety"]["live_trading_enabled"] is False
    assert len(first["record_sha256"]) == 64
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0

    second_response = client.post(
        "/api/v1/research/date-range-backtests", json=body
    )
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert second["id"] == first["id"]
    assert second["record_sha256"] == first["record_sha256"]
    assert second["created_at"] == first["created_at"]

    listed = client.get("/api/v1/research/date-range-backtests")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == first["id"]

    detail = client.get(
        f"/api/v1/research/date-range-backtests/{first['id']}"
    )
    assert detail.status_code == 200
    assert detail.json() == first

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with client.app.state.database.connection() as connection:
            connection.execute(
                "UPDATE date_range_backtest_records SET created_at = ? WHERE id = ?",
                ("2099-01-01T00:00:00Z", first["id"]),
            )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with client.app.state.database.connection() as connection:
            connection.execute(
                "DELETE FROM date_range_backtest_records WHERE id = ?",
                (first["id"],),
            )


def test_report_export_writes_summary_candidate_equity_trade_csv_and_markdown(
    tmp_path,
) -> None:
    from emergenzix_api.date_range_backtest import DATE_RANGE_BACKTEST_PROTOCOL_VERSION
    from emergenzix_api.date_range_report import write_report_artifacts

    report = _run(_series())
    record = {
        "id": "a" * 64,
        "record_sha256": "b" * 64,
        "protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "report": report,
    }
    paths = write_report_artifacts(record, tmp_path / "btc-week.json")

    assert set(paths) == {
        "json",
        "markdown",
        "summary_csv",
        "candidates_csv",
        "equity_csv",
        "trades_csv",
    }
    assert (tmp_path / "btc-week.json").exists()
    assert (tmp_path / "btc-week.md").exists()
    assert "Net return" in (tmp_path / "btc-week.md").read_text(encoding="utf-8")
    assert "total_return_pct" in (tmp_path / "btc-week.summary.csv").read_text(encoding="utf-8-sig")
    assert len((tmp_path / "btc-week.candidates.csv").read_text(encoding="utf-8-sig").splitlines()) == 7
    assert len((tmp_path / "btc-week.equity.csv").read_text(encoding="utf-8-sig").splitlines()) == 44
    assert "net_pnl" in (tmp_path / "btc-week.trades.csv").read_text(encoding="utf-8-sig")


def test_report_export_requires_json_extension(tmp_path) -> None:
    from emergenzix_api.date_range_report import write_report_artifacts

    with pytest.raises(ValueError, match="end in .json"):
        write_report_artifacts({}, tmp_path / "report.csv")


def test_date_range_backtest_api_does_not_persist_incomplete_data(client, monkeypatch) -> None:
    import emergenzix_api.date_range_backtest as service

    bars = _series()
    del bars[200]
    monkeypatch.setattr(
        service,
        "fetch_binance_btc_4h_archive",
        lambda **_kwargs: [bar.model_dump(mode="json") for bar in bars],
    )
    response = client.post(
        "/api/v1/research/date-range-backtests",
        json={"start_date": "2025-10-21"},
    )
    assert response.status_code == 422
    assert "gap" in response.json()["detail"] or "incomplete" in response.json()["detail"]
    assert client.get("/api/v1/datasets").json() == []


def test_date_range_backtest_api_rejects_invalid_period(client) -> None:
    response = client.post(
        "/api/v1/research/date-range-backtests",
        json={"start_date": "2025-10-21", "duration_days": 0},
    )
    assert response.status_code == 422
