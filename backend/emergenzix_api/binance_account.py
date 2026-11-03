"""Read-only Binance Spot account summary with fail-closed permissions.

Only signed GET endpoints are implemented.  This module has no order,
transfer, withdrawal, user-stream, or credential-management method.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx


BINANCE_API_BASE_URL = "https://api.binance.com"


class BinanceAccountUnavailable(RuntimeError):
    """Raised when the read-only account summary cannot be trusted."""


@dataclass(frozen=True)
class BinanceReadOnlyCredentials:
    api_key: str
    api_secret: str
    recv_window_ms: int = 5_000

    @classmethod
    def from_env(cls) -> BinanceReadOnlyCredentials | None:
        api_key = _secret_from_env(
            "EMERGENZIX_BINANCE_API_KEY", "EMERGENZIX_BINANCE_API_KEY_FILE"
        )
        api_secret = _secret_from_env(
            "EMERGENZIX_BINANCE_API_SECRET", "EMERGENZIX_BINANCE_API_SECRET_FILE"
        )
        if not api_key and not api_secret:
            return None
        if not api_key or not api_secret:
            raise ValueError("Binance read-only credentials must be configured as a pair")
        recv_window = int(os.getenv("EMERGENZIX_BINANCE_RECV_WINDOW_MS", "5000"))
        if not 1_000 <= recv_window <= 60_000:
            raise ValueError("EMERGENZIX_BINANCE_RECV_WINDOW_MS must be 1000..60000")
        return cls(api_key=api_key, api_secret=api_secret, recv_window_ms=recv_window)


def _secret_from_env(value_name: str, file_name: str) -> str:
    direct = os.getenv(value_name, "").strip()
    secret_path = os.getenv(file_name, "").strip()
    if direct and secret_path:
        raise ValueError(f"Configure only one of {value_name} or {file_name}")
    if secret_path:
        try:
            direct = Path(secret_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Unable to read {file_name}") from exc
    return direct


class BinanceReadOnlyAccount:
    """Fetch a bounded account summary without exposing signing material."""

    def __init__(
        self,
        credentials: BinanceReadOnlyCredentials | None,
        *,
        transport: httpx.BaseTransport | None = None,
        clock_ms: Callable[[], int] | None = None,
    ):
        self._credentials = credentials
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))

    @property
    def configured(self) -> bool:
        return self._credentials is not None

    def _signed_get(self, path: str, extra: dict[str, str] | None = None) -> Any:
        credentials = self._credentials
        if credentials is None:
            raise BinanceAccountUnavailable("Binance account connection is not configured")
        params = {
            "timestamp": str(self._clock_ms()),
            "recvWindow": str(credentials.recv_window_ms),
            **(extra or {}),
        }
        query = urlencode(params)
        signature = hmac.new(
            credentials.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        try:
            with httpx.Client(
                base_url=BINANCE_API_BASE_URL,
                timeout=10.0,
                transport=self._transport,
            ) as client:
                response = client.get(
                    path + "?" + query + "&signature=" + signature,
                    headers={"X-MBX-APIKEY": credentials.api_key},
                )
        except httpx.HTTPError as exc:
            raise BinanceAccountUnavailable("Binance account endpoint is unavailable") from exc
        if response.status_code != 200:
            raise BinanceAccountUnavailable(
                f"Binance account endpoint returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BinanceAccountUnavailable("Binance account endpoint returned invalid JSON") from exc

    def summary(self) -> dict[str, Any]:
        permissions = self._signed_get("/sapi/v1/account/apiRestrictions")
        if permissions.get("enableReading") is not True:
            raise BinanceAccountUnavailable("Binance API key does not allow account reading")
        if permissions.get("enableWithdrawals") is not False:
            raise BinanceAccountUnavailable(
                "Binance API key is rejected because withdrawal permission is enabled"
            )
        account = self._signed_get(
            "/api/v3/account", {"omitZeroBalances": "true"}
        )
        balances = []
        quote_balance = {"asset": "USDT", "free": "0", "locked": "0", "total": "0"}
        for item in account.get("balances", []):
            asset = str(item.get("asset", "")).strip().upper()
            try:
                free = Decimal(str(item.get("free", "0")))
                locked = Decimal(str(item.get("locked", "0")))
            except InvalidOperation as exc:
                raise BinanceAccountUnavailable("Binance returned an invalid balance") from exc
            if not asset or free < 0 or locked < 0:
                raise BinanceAccountUnavailable("Binance returned an invalid balance")
            total = free + locked
            if total == 0:
                continue
            balance = {
                "asset": asset,
                "free": _decimal_text(free),
                "locked": _decimal_text(locked),
                "total": _decimal_text(total),
            }
            balances.append(balance)
            if asset == "USDT":
                quote_balance = balance
        balances.sort(key=lambda item: item["asset"])
        return {
            "status": "connected",
            "exchange": "binance",
            "account_type": str(account.get("accountType", "SPOT")),
            "quote_balance": quote_balance,
            "nonzero_asset_count": len(balances),
            "permissions": {
                "reading": True,
                "withdrawals": False,
                "credential_spot_trading": bool(
                    permissions.get("enableSpotAndMarginTrading")
                ),
                "credential_futures": bool(permissions.get("enableFutures")),
                "ip_restricted": bool(permissions.get("ipRestrict")),
                "product_order_permission": False,
            },
            "mode": "READ_ONLY",
            "live_trading_enabled": False,
        }


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text

