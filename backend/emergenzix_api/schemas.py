"""Request validation models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SYMBOL_PATTERN = r"^[A-Z0-9]{3,20}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    strategy_type: Literal["sma_crossover"] = "sma_crossover"
    fast_window: int = Field(ge=2, le=200)
    slow_window: int = Field(ge=3, le=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must contain a non-whitespace character")
        return normalized

    @model_validator(mode="after")
    def validate_windows(self) -> "StrategyCreate":
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        return self


class Bar(StrictModel):
    timestamp: datetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Bar":
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be <= open, close, and high")
        return self


class DatasetCreate(StrictModel):
    """A validated OHLCV dataset to register as an immutable content-addressed record."""

    source: Literal["user_supplied", "ccxt_public_ohlcv", "synthetic_demo"] = "user_supplied"
    exchange: Literal["binance", "bybit", "okx", "kraken", "coinbase"] | None = None
    symbol: str = Field(
        min_length=3,
        max_length=30,
        pattern=r"^[A-Za-z0-9]{1,20}(?:/[A-Za-z0-9]{1,10})?$",
    )
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] | None = None
    bars: list[Bar] = Field(min_length=5, max_length=10_000)

    @field_validator("symbol")
    @classmethod
    def normalize_dataset_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_dataset(self) -> "DatasetCreate":
        timestamps = [bar.timestamp for bar in self.bars]
        if any(
            current <= previous
            for previous, current in zip(timestamps, timestamps[1:])
        ):
            raise ValueError("bars must be in strictly increasing timestamp order")
        if self.source == "ccxt_public_ohlcv":
            if not self.exchange or not self.timeframe:
                raise ValueError(
                    "CCXT datasets must record exchange and timeframe metadata"
                )
        return self


class BacktestCreate(StrictModel):
    strategy_id: str = Field(min_length=1, max_length=64)
    dataset_id: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    bars: list[Bar] | None = Field(default=None, min_length=5, max_length=10_000)
    initial_cash: float = Field(default=10_000.0, gt=0, le=1_000_000_000)
    fee_bps: float = Field(default=10.0, ge=0, le=1_000, allow_inf_nan=False)
    slippage_bps: float = Field(default=5.0, ge=0, le=1_000, allow_inf_nan=False)
    data_source: Literal["user_supplied", "ccxt_public_ohlcv", "synthetic_demo"] = "user_supplied"
    data_exchange: str | None = Field(default=None, min_length=1, max_length=32)
    data_symbol: str | None = Field(default=None, min_length=3, max_length=30)
    data_timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] | None = None

    @model_validator(mode="after")
    def validate_input_source(self) -> "BacktestCreate":
        if (self.dataset_id is None) == (self.bars is None):
            raise ValueError("provide exactly one of dataset_id or bars")

        if self.dataset_id is not None:
            if (
                self.data_exchange is not None
                or self.data_symbol is not None
                or self.data_timeframe is not None
                or "data_source" in self.model_fields_set
            ):
                raise ValueError(
                    "dataset_id is the source of truth; do not supply data_* overrides"
                )
            return self

        if self.bars is None:
            raise ValueError("bars are required when dataset_id is not provided")

        timestamps = [bar.timestamp for bar in self.bars]
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise ValueError("bars must be in strictly increasing timestamp order")
        if self.data_source == "ccxt_public_ohlcv":
            if not self.data_exchange or not self.data_symbol or not self.data_timeframe:
                raise ValueError(
                    "CCXT backtests must record data_exchange, data_symbol, and data_timeframe"
                )
        return self


class PaperOrderCreate(StrictModel):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0, le=1_000_000_000, allow_inf_nan=False)
    reference_price: float = Field(gt=0, le=1_000_000_000, allow_inf_nan=False)
    strategy_id: str | None = Field(default=None, max_length=64)
