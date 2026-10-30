from __future__ import annotations

from datetime import datetime, timezone

import pytest

from emergenzix_api.nemt_strategy.registry import (
    DEFAULT_STRATEGY_ID,
    data_fingerprint,
    get_strategy,
    list_strategies,
    report_fingerprint,
)


def test_default_is_reviewed_v44_and_cannot_trade_live() -> None:
    strategy = get_strategy()

    assert strategy.strategy_id == DEFAULT_STRATEGY_ID
    assert strategy.version == "4.4.0"
    assert strategy.execution_mode == "BACKTEST_ONLY"
    assert strategy.live_enabled is False
    assert strategy.variants == ("v4a", "v4b", "v4c")
    assert strategy.parameters["router"]["annual_trend_usage"] == "research_label_only"
    assert list_strategies() == (strategy,)
    assert len(strategy.parameter_fingerprint) == 64
    assert len(strategy.evidence_fingerprint) == 64


def test_unknown_strategy_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown NEMT strategy"):
        get_strategy("sma-donchian-baseline")


def test_fingerprints_are_deterministic_and_content_sensitive() -> None:
    bar = {
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": 12,
    }
    first = data_fingerprint([bar])
    second = data_fingerprint([dict(reversed(list(bar.items())))])
    changed = data_fingerprint([{**bar, "close": 106}])

    assert first == second
    assert changed != first
    assert report_fingerprint({"b": 2, "a": 1}) == report_fingerprint({"a": 1, "b": 2})
    assert report_fingerprint({"a": 1, "report_fingerprint": "ignored"}) == report_fingerprint({"a": 1})
