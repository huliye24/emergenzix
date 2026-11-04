from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.model_signal_artifacts import build_signal_record
from emergenzix_api.schemas import Bar
from emergenzix_api.strategy_evidence_pipeline import (
    build_model_prediction, build_strategy_analysis, merge_node_results,
)


NOW = datetime(2026, 11, 4, 4, tzinfo=timezone.utc)


def _market():
    bars = []
    start = NOW - timedelta(hours=4 * 60)
    for index in range(60):
        close = 70_000 + index * 10
        bars.append({"timestamp": (start + timedelta(hours=4 * index)).isoformat(),
                     "open": close - 5, "high": close + 20, "low": close - 20,
                     "close": close, "volume": 100 + index})
    return {"exchange": "binance", "symbol": "BTCUSDT", "timeframe": "4h", "bars": bars}


def _signal(strategy):
    payload = _market()
    bars = [Bar.model_validate(item) for item in payload["bars"]]
    signal = build_signal_record(
        family="Chronos", name="amazon/chronos-bolt-small", version="2.3.2",
        source_revision="gpu-revision", artifact_sha256="a" * 64, bars=bars,
        symbol="BTCUSDT", timeframe="4h", generated_at=NOW, direction="UP", score=0.5,
        target_at=bars[-1].timestamp + timedelta(hours=4),
        prediction_details={"forecast_close": 71_000.0, "predicted_return": 0.01},
    )
    return build_model_prediction({"signals": [signal]}, expected_market=strategy["market"],
                                  generated_at=NOW, source_revision="gpu-revision")


def test_cpu_and_gpu_results_merge_for_exact_same_snapshot():
    strategy = build_strategy_analysis(_market(), generated_at=NOW, source_revision="cpu-revision")
    prediction = _signal(strategy)
    evidence = merge_node_results(strategy, prediction, now=NOW)
    assert evidence["strategy"]["name"] == "turtle_breakout_20_10"
    assert evidence["prediction"]["direction"] == "UP"
    assert [item["role"] for item in evidence["provenance"]] == ["strategy_analysis", "model_prediction"]
    assert evidence["order_permission"] is False


def test_merge_rejects_different_gpu_snapshot():
    strategy = build_strategy_analysis(_market(), generated_at=NOW, source_revision="cpu-revision")
    prediction = _signal(strategy)
    prediction["market"]["bars_sha256"] = "b" * 64
    from emergenzix_api.strategy_evidence_pipeline import record_sha256
    prediction["record_sha256"] = record_sha256(prediction)
    with pytest.raises(ValueError, match="market identities"):
        merge_node_results(strategy, prediction, now=NOW)


def test_merge_rejects_stale_and_tampered_results():
    strategy = build_strategy_analysis(_market(), generated_at=NOW - timedelta(hours=9),
                                       source_revision="cpu-revision")
    prediction = _signal(strategy)
    with pytest.raises(ValueError, match="strategy_analysis is stale"):
        merge_node_results(strategy, prediction, now=NOW)
    fresh = build_strategy_analysis(_market(), generated_at=NOW, source_revision="cpu-revision")
    fresh["strategy"]["decision"] = "BUY"
    with pytest.raises(ValueError, match="hash mismatch"):
        merge_node_results(fresh, prediction, now=NOW)


def test_strategy_uses_only_prior_twenty_bars_for_entry():
    payload = _market()
    prior_high = max(item["high"] for item in payload["bars"][-21:-1])
    payload["bars"][-1]["close"] = prior_high + 1
    payload["bars"][-1]["high"] = prior_high + 2
    result = build_strategy_analysis(payload, generated_at=NOW, source_revision="cpu-revision")
    assert result["strategy"]["decision"] == "BUY"
    assert result["strategy"]["entry_level"] == prior_high
