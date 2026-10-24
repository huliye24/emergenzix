"""Configuration for the Emergenzix API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime settings with safe, paper-only defaults."""

    db_path: str
    initial_paper_cash: float = 100_000.0
    max_order_notional: float = 5_000.0
    paper_fee_bps: float = 10.0
    market_data_cache_ttl_seconds: int = 30
    market_data_cache_max_entries: int = 256
    notification_db_path: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        default_db = _REPOSITORY_ROOT / "data" / "emergenzix.db"
        db_path = os.environ.get("EMERGENZIX_DB_PATH", str(default_db))
        default_notification_db = (
            Path(db_path).expanduser().parent / "notification-outbox.sqlite3"
        )
        return cls(
            db_path=db_path,
            notification_db_path=os.environ.get(
                "EMERGENZIX_NOTIFICATION_DB_PATH", str(default_notification_db)
            ),
            initial_paper_cash=_positive_float(
                "EMERGENZIX_INITIAL_PAPER_CASH", 100_000.0
            ),
            max_order_notional=_positive_float(
                "EMERGENZIX_MAX_ORDER_NOTIONAL", 5_000.0
            ),
            paper_fee_bps=_non_negative_float("EMERGENZIX_PAPER_FEE_BPS", 10.0),
            market_data_cache_ttl_seconds=_non_negative_int(
                "EMERGENZIX_MARKET_DATA_CACHE_TTL_SECONDS", 30
            ),
            market_data_cache_max_entries=_positive_int(
                "EMERGENZIX_MARKET_DATA_CACHE_MAX_ENTRIES", 256
            ),
        )


def _positive_float(name: str, default: float) -> float:
    value = float(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _non_negative_float(name: str, default: float) -> float:
    value = float(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value



def _non_negative_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be at least one")
    return value
