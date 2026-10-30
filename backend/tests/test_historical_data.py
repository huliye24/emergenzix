from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone

import httpx
import pytest

from emergenzix_api.historical_data import (
    HistoricalArchiveUnavailable,
    _fetch_checked_archive,
    _parse_kline_archive,
    fetch_binance_spot_4h_archive,
)


def _zip_csv(rows: list[list[object]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        content = "\n".join(",".join(str(value) for value in row) for row in rows)
        archive.writestr("BTCUSDT-4h-2024-03.csv", content)
    return buffer.getvalue()


class _Response:
    def __init__(self, status_code: int, *, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self.content = content
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://data.binance.vision/test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("HTTP error", request=request, response=response)


class _Client:
    def __init__(self, responses: dict[str, _Response]):
        self.responses = responses

    def get(self, url: str):
        return self.responses.get(url, _Response(404))


def test_parse_kline_archive_normalizes_timestamps_and_filters_range():
    march_1_ms = int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
    march_2_ms = int(datetime(2024, 3, 2, tzinfo=timezone.utc).timestamp() * 1000)
    payload = _zip_csv(
        [
            [march_1_ms, "67000", "68000", "66000", "67500", "100"],
            [march_2_ms, "67500", "69000", "67000", "68000", "200"],
        ]
    )

    bars = _parse_kline_archive(
        payload,
        start_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        end_at=datetime(2024, 3, 1, 23, 59, tzinfo=timezone.utc),
    )
    assert len(bars) == 1
    assert bars[0]["timestamp"] == "2024-03-01T00:00:00Z"
    assert bars[0]["close"] == 67500.0


def test_fetch_checked_archive_requires_matching_sha256():
    payload = _zip_csv(
        [[
            int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp() * 1000),
            "67000", "68000", "66000", "67500", "100"
        ]]
    )
    url = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-03.zip"
    digest = hashlib.sha256(payload).hexdigest()
    client = _Client({
        url: _Response(200, content=payload),
        url + ".CHECKSUM": _Response(200, text=f"{digest}  BTCUSDT-4h-2024-03.zip"),
    })
    assert _fetch_checked_archive(client, url) == payload

    bad_client = _Client({
        url: _Response(200, content=payload),
        url + ".CHECKSUM": _Response(200, text=f"{'0' * 64}  BTCUSDT-4h-2024-03.zip"),
    })
    with pytest.raises(HistoricalArchiveUnavailable, match="SHA-256 mismatch"):
        _fetch_checked_archive(bad_client, url)


def test_spot_archive_symbol_allowlist_fails_closed():
    with pytest.raises(ValueError, match="unsupported Binance archive symbol"):
        fetch_binance_spot_4h_archive(
            symbol="DOGEUSDT",
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )


def test_eth_is_in_the_archive_allowlist_but_unknown_interval_fails_first():
    from emergenzix_api.historical_data import fetch_binance_spot_kline_archive

    with pytest.raises(ValueError, match="unsupported Binance archive interval"):
        fetch_binance_spot_kline_archive(
            symbol="ETHUSDT", interval="7m",
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
