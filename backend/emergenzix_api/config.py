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

    @classmethod
    def from_env(cls) -> "Settings":
        default_db = _REPOSITORY_ROOT / "data" / "emergenzix.db"
        return cls(
            db_path=os.environ.get("EMERGENZIX_DB_PATH", str(default_db)),
            initial_paper_cash=_positive_float(
                "EMERGENZIX_INITIAL_PAPER_CASH", 100_000.0
            ),
            max_order_notional=_positive_float(
                "EMERGENZIX_MAX_ORDER_NOTIONAL", 5_000.0
            ),
            paper_fee_bps=_non_negative_float("EMERGENZIX_PAPER_FEE_BPS", 10.0),
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
