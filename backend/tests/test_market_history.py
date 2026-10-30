from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.market_history import (
    MarketHistoryUnavailable,
    build_binance_verified_history,
    fetch_binance_public_tail,
    latest_completed_4h_open,
)


UTC = timezone.utc


def _bar(stamp: datetime, value: float) -> dict[str, object]:
    return {
        "timestamp": stamp.isoformat(), "open": value, "high": value + 2,
        "low": value - 2, "close": value + 1, "volume": 100 + value,
    }


def _history_inputs(*, gap_at: datetime | None = None, mismatch: bool = False):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    now = datetime(2026, 10, 30, 5, 15, tzinfo=UTC)
    completed = latest_completed_4h_open(now)
    all_bars = []
    stamp = start
    while stamp <= completed + timedelta(hours=4):
        if stamp != gap_at:
            all_bars.append(_bar(stamp, float(len(all_bars) + 1000)))
        stamp += timedelta(hours=4)
    archive_end = completed.replace(hour=0) - timedelta(days=2) + timedelta(hours=20)
    archive = [bar.copy() for bar in all_bars if datetime.fromisoformat(str(bar["timestamp"])) <= archive_end]
    public = [bar.copy() for bar in all_bars[-1000:]]
    if mismatch:
        public[10]["close"] = float(public[10]["close"]) + 10
        public[10]["high"] = float(public[10]["high"]) + 10
    def archive_fetcher(**kwargs):
        return archive

    def public_fetcher(**kwargs):
        return {"bars": public}

    return start, now, archive_fetcher, public_fetcher


def test_builds_long_fresh_history_and_excludes_forming_candle() -> None:
    start, now, archive_fetcher, public_fetcher = _history_inputs()
    result = build_binance_verified_history(
        symbol="BNBUSDT", start_at=start, now=now,
        archive_fetcher=archive_fetcher, public_fetcher=public_fetcher,
    )
    assert result["symbol"] == "BNB/USDT"
    assert datetime.fromisoformat(str(result["bars"][0]["timestamp"])) == start
    assert datetime.fromisoformat(str(result["bars"][-1]["timestamp"])) == latest_completed_4h_open(now)
    assert result["overlap_bars"] > 6
    assert result["forming_candle_excluded"] is True


def test_rejects_archive_public_disagreement() -> None:
    start, now, archive_fetcher, public_fetcher = _history_inputs(mismatch=True)
    with pytest.raises(MarketHistoryUnavailable, match="mismatch"):
        build_binance_verified_history(
            symbol="BTCUSDT", start_at=start, now=now,
            archive_fetcher=archive_fetcher, public_fetcher=public_fetcher,
        )


def test_rejects_internal_four_hour_gap() -> None:
    gap = datetime(2025, 1, 1, tzinfo=UTC)
    start, now, archive_fetcher, public_fetcher = _history_inputs(gap_at=gap)
    with pytest.raises(MarketHistoryUnavailable, match=r"non-contiguous.*missing=1"):
        build_binance_verified_history(
            symbol="BTCUSDT", start_at=start, now=now,
            archive_fetcher=archive_fetcher, public_fetcher=public_fetcher,
        )


def test_requires_two_year_minimum_coverage() -> None:
    with pytest.raises(ValueError, match="730 days"):
        build_binance_verified_history(
            symbol="BTCUSDT", start_at=datetime(2026, 1, 1, tzinfo=UTC),
            now=datetime(2026, 10, 30, tzinfo=UTC),
        )


def test_public_tail_uses_market_data_endpoint(monkeypatch) -> None:
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                [1_700_000_000_000 + index * 900_000, "100", "102", "99", "101", "10"]
                for index in range(5)
            ]

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return Response()

    monkeypatch.setattr("emergenzix_api.market_history.httpx.get", fake_get)
    result = fetch_binance_public_tail(symbol="ETHUSDT", interval="15m", limit=5)
    assert seen["url"] == "https://data-api.binance.vision/api/v3/klines"
    assert seen["params"] == {"symbol": "ETHUSDT", "interval": "15m", "limit": 5}
    assert result["bars_count"] == 5
