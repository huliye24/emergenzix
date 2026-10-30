from datetime import datetime, timedelta, timezone

from emergenzix_api.database import Database
from emergenzix_api.market_history_sync import sync_symbol


def test_sync_registers_an_immutable_reconciled_dataset(tmp_path, monkeypatch) -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = [
        {
            "timestamp": start + timedelta(hours=4 * index),
            "open": 100 + index, "high": 102 + index, "low": 99 + index,
            "close": 101 + index, "volume": 10 + index,
        }
        for index in range(8)
    ]
    monkeypatch.setattr(
        "emergenzix_api.market_history_sync.build_binance_verified_history",
        lambda **kwargs: {
            "symbol": "BTC/USDT", "bars": bars,
            "forming_candle_excluded": True,
            "archive_checksum_verified": True,
            "public_tail_reconciled": True,
        },
    )
    database = Database(str(tmp_path / "research.db"))
    first = sync_symbol(db=database, symbol="BTCUSDT", start_at=start)
    second = sync_symbol(db=database, symbol="BTCUSDT", start_at=start)
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert second["dataset_id"] == first["dataset_id"]
    assert first["archive_checksum_verified"] is True
