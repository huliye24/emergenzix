"""Side-effect-free planning for bounded, reproducible weekly BTC research batches.

Planning never fetches OHLCV, reruns a strategy, persists an experiment, or creates orders.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .date_range_backtest import DATE_RANGE_BACKTEST_PROTOCOL_VERSION
from .schemas import StrictModel

BATCH_PLAN_VERSION = "btc-weekly-research-batch-plan-v1"
MAX_BATCH_SIZE = 12
WEEK_DAYS = 7
BARS_PER_DAY = 6


def _hash(payload: Any) -> str:
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class WeeklyResearchBatchPlanRequest(StrictModel):
    start_dates: list[date] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    duration_days: Literal[7] = 7
    training_days: int = Field(default=90, ge=60, le=365)
    initial_cash: float = Field(default=10_000.0, gt=0, le=1_000_000_000, allow_inf_nan=False)
    fee_bps: float = Field(default=10.0, ge=0, le=1_000, allow_inf_nan=False)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000, allow_inf_nan=False)

    @field_validator("start_dates", mode="before")
    @classmethod
    def only_calendar_dates(cls, value: object) -> object:
        """Reject coerced timestamps, datetimes, Unicode digits and path-like values."""
        if not isinstance(value, list):
            raise ValueError("start_dates must be a JSON array")
        for item in value:
            if (
                not isinstance(item, str)
                or len(item) != 10
                or item[4] != "-"
                or item[7] != "-"
                or any(
                    character not in "0123456789"
                    for index, character in enumerate(item)
                    if index not in (4, 7)
                )
            ):
                raise ValueError("each start_date must be YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def require_disjoint_weeks(self) -> "WeeklyResearchBatchPlanRequest":
        ordered = sorted(self.start_dates)
        if len(ordered) != len(set(ordered)):
            raise ValueError("start_dates must be unique")
        for previous, current in zip(ordered, ordered[1:]):
            if current - previous < timedelta(days=WEEK_DAYS):
                raise ValueError("weekly test intervals must not overlap")
        return self


def plan_weekly_research_batch(
    request: WeeklyResearchBatchPlanRequest,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic plan, NOT runnable or persisted job state."""
    cutoff = now if now is not None else datetime.now(timezone.utc)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cutoff = cutoff.astimezone(timezone.utc)

    sorted_dates = sorted(request.start_dates)
    ends = [_midnight(start) + timedelta(days=WEEK_DAYS) for start in sorted_dates]
    if any(end > cutoff for end in ends):
        raise ValueError("each weekly test interval must finish in the past")

    config = {
        "plan_version": BATCH_PLAN_VERSION,
        "backtest_protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "market": {
            "exchange": "binance", "market_type": "spot",
            "symbol": "BTC/USDT", "timeframe": "4h",
        },
        "start_dates": [value.isoformat() for value in sorted_dates],
        "duration_days": WEEK_DAYS,
        "training_days": request.training_days,
        "initial_cash": float(request.initial_cash),
        "fee_bps": float(request.fee_bps),
        "slippage_bps": float(request.slippage_bps),
    }
    batch_id = _hash(config)
    periods = []
    for position, (start, test_end) in enumerate(zip(sorted_dates, ends), start=1):
        payload = {
            "start_date": start.isoformat(),
            "duration_days": WEEK_DAYS,
            "training_days": request.training_days,
            "initial_cash": float(request.initial_cash),
            "fee_bps": float(request.fee_bps),
            "slippage_bps": float(request.slippage_bps),
        }
        periods.append({
            "sequence": position,
            "planned_job_id": _hash({
                "batch_id": batch_id, "sequence": position, "backtest_request": payload,
            }),
            "state": "planned_only",
            "training_start_at": _iso(_midnight(start) - timedelta(days=request.training_days)),
            "test_start_at": _iso(_midnight(start)),
            "test_end_at_exclusive": _iso(test_end),
            "required_training_candles": request.training_days * BARS_PER_DAY,
            "required_test_candles": WEEK_DAYS * BARS_PER_DAY,
            "total_required_candles": (request.training_days + WEEK_DAYS) * BARS_PER_DAY,
            "backtest_request": payload,
            "saved_record_id": None,
            "dataset_coverage_verified": False,
        })

    return {
        "plan_version": BATCH_PLAN_VERSION,
        "backtest_protocol_version": DATE_RANGE_BACKTEST_PROTOCOL_VERSION,
        "batch_id": batch_id,
        "plan_sha256": batch_id,
        "selection": config,
        "periods_count": len(periods),
        "periods": periods,
        "all_test_windows_nonoverlapping": True,
        "requires_market_data_coverage_verification": True,
        "execution_state": "not_started",
        "plan_persisted": False,
        "backtests_executed": 0,
        "orders_created": False,
        "strategy_promotion_allowed": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
        "limitations": [
            "Planning does not fetch historical candles or verify data coverage.",
            "A future worker must provide durable job states, leases, idempotency and retry controls.",
            "Disjoint test windows can share overlapping training samples and correlated conditions.",
            "Standalone historical returns cannot be compounded into a continuous portfolio curve.",
            "No exchange or PAPER orders are created or authorized.",
        ],
    }
