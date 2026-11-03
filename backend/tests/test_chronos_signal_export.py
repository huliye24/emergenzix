from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.chronos_signal_export import _completed_bars


def _payload() -> dict[str, object]:
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(51):
        close = 100.0 + index
        bars.append({
            "timestamp": (start + timedelta(hours=index)).isoformat(),
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000.0,
        })
    return {"symbol": "BTC/USDT", "timeframe": "1h", "bars": bars}


def test_completed_bars_excludes_forming_tail_when_requested() -> None:
    bars = _completed_bars(_payload(), exclude_final_bar=True)
    assert len(bars) == 50
    assert bars[-1].close == 149.0


def test_completed_bars_rejects_unsorted_input() -> None:
    payload = _payload()
    payload["bars"][20], payload["bars"][21] = payload["bars"][21], payload["bars"][20]
    with pytest.raises(ValueError, match="strictly ordered"):
        _completed_bars(payload, exclude_final_bar=False)
