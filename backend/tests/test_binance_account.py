from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from emergenzix_api.binance_account import (
    BinanceAccountUnavailable,
    BinanceReadOnlyAccount,
    BinanceReadOnlyCredentials,
)
from emergenzix_api.binance_account_api import account_summary


def _transport(*, withdrawals: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["X-MBX-APIKEY"] == "public-key-id"
        query = parse_qs(urlparse(str(request.url)).query)
        signature = query.pop("signature")[0]
        unsigned = "&".join(
            f"{key}={value[0]}" for key, value in query.items()
        )
        expected = hmac.new(
            b"private-signing-secret", unsigned.encode(), hashlib.sha256
        ).hexdigest()
        assert signature == expected
        if request.url.path.endswith("apiRestrictions"):
            return httpx.Response(
                200,
                json={
                    "enableReading": True,
                    "enableWithdrawals": withdrawals,
                    "enableSpotAndMarginTrading": True,
                    "enableFutures": True,
                    "ipRestrict": True,
                },
            )
        return httpx.Response(
            200,
            json={
                "accountType": "SPOT",
                "balances": [
                    {"asset": "BTC", "free": "0.01", "locked": "0"},
                    {"asset": "USDT", "free": "12.3400", "locked": "0.6600"},
                    {"asset": "ETH", "free": "0", "locked": "0"},
                ],
            },
        )

    return httpx.MockTransport(handler)


def _reader(*, withdrawals: bool = False) -> BinanceReadOnlyAccount:
    return BinanceReadOnlyAccount(
        BinanceReadOnlyCredentials("public-key-id", "private-signing-secret"),
        transport=_transport(withdrawals=withdrawals),
        clock_ms=lambda: 1_700_000_000_000,
    )


def test_read_only_summary_returns_bounded_balances_and_permissions() -> None:
    summary = _reader().summary()

    assert summary["quote_balance"] == {
        "asset": "USDT",
        "free": "12.34",
        "locked": "0.66",
        "total": "13",
    }
    assert summary["nonzero_asset_count"] == 2
    assert summary["permissions"] == {
        "reading": True,
        "withdrawals": False,
        "credential_spot_trading": True,
        "credential_futures": True,
        "ip_restricted": True,
        "product_order_permission": False,
    }
    assert summary["live_trading_enabled"] is False
    assert "public-key-id" not in json.dumps(summary)
    assert "private-signing-secret" not in json.dumps(summary)


def test_withdrawal_enabled_key_is_rejected_before_balance_request() -> None:
    with pytest.raises(BinanceAccountUnavailable, match="withdrawal permission"):
        _reader(withdrawals=True).summary()


def test_missing_credentials_fail_closed() -> None:
    with pytest.raises(BinanceAccountUnavailable, match="not configured"):
        BinanceReadOnlyAccount(None).summary()


def test_account_summary_route_is_sanitized() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(binance_account_reader=_reader())
        )
    )

    response = account_summary(request)

    assert response["quote_balance"]["total"] == "13"
    assert "secret" not in json.dumps(response).lower()

