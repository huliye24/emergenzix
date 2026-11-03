"""Offline GS Quant time-series analytics behind an optional dependency.

This module deliberately does not create a GS session, call Marquee APIs, fetch
market data, or participate in strategy promotion or order creation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any


def analyze_prices(
    prices: Mapping[datetime, float],
    *,
    benchmark_prices: Mapping[datetime, float] | None = None,
    window: int = 14,
) -> dict[str, Any]:
    """Calculate a small, offline diagnostic set with ``gs_quant.timeseries``.

    Inputs must be positive finite prices keyed by unique timezone-aware
    timestamps. Results are diagnostics only and grant no execution permission.
    """
    if window < 2:
        raise ValueError("window must be at least 2")
    _validate_prices(prices, minimum=window + 1, label="prices")
    if benchmark_prices is not None:
        _validate_prices(benchmark_prices, minimum=window + 1, label="benchmark_prices")

    try:
        import pandas as pd
        import gs_quant
        import gs_quant.timeseries as ts
    except ImportError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            "GS Quant analytics require backend/requirements-gs-quant.txt"
        ) from exc

    series = pd.Series(
        {timestamp: float(value) for timestamp, value in sorted(prices.items())},
        dtype=float,
    )
    bands = ts.bollinger_bands(series, window)
    result: dict[str, Any] = {
        "source": "gs-quant",
        "source_version": gs_quant.__version__,
        "observations": len(series),
        "window": window,
        "latest_timestamp": series.index[-1].isoformat(),
        "realized_volatility_pct": _latest_finite(ts.volatility(series, window)),
        "max_drawdown_fraction": _latest_finite(ts.max_drawdown(series)),
        "z_score": _latest_finite(ts.zscores(series, window)),
        # GS Quant returns the lower band in column 0 and upper band in column 1.
        "bollinger_lower": _latest_finite(bands.iloc[:, 0]),
        "bollinger_upper": _latest_finite(bands.iloc[:, 1]),
        "rsi": _latest_finite(ts.relative_strength_index(series, window)),
        "beta": None,
    }

    if benchmark_prices is not None:
        benchmark = pd.Series(
            {
                timestamp: float(value)
                for timestamp, value in sorted(benchmark_prices.items())
                if timestamp in prices
            },
            dtype=float,
        )
        aligned = pd.concat([series.rename("asset"), benchmark.rename("benchmark")], axis=1).dropna()
        if len(aligned) < window + 1:
            raise ValueError(f"asset and benchmark need at least {window + 1} aligned observations")
        result["beta"] = _latest_finite(
            ts.beta(aligned["asset"], aligned["benchmark"], window)
        )

    return result


def _validate_prices(
    values: Mapping[datetime, float], *, minimum: int, label: str
) -> None:
    if len(values) < minimum:
        raise ValueError(f"{label} need at least {minimum} observations")
    for timestamp, value in values.items():
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError(f"{label} timestamps must be timezone-aware datetimes")
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"{label} values must be positive and finite")


def _latest_finite(series: Any) -> float:
    for value in reversed(series.tolist()):
        number = float(value)
        if math.isfinite(number):
            return number
    raise ValueError("GS Quant returned no finite diagnostic value")
