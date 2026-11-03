from __future__ import annotations

from emergenzix_api.deepseek_analysis import DeepSeekAnalysisError
from emergenzix_api.market_ai import attach_deepseek_market_analysis


def _snapshot(*, stale: bool = False) -> dict:
    return {
        "symbol": "ETH/USDT",
        "timeframe": "1h",
        "observed_at": "2026-11-03T12:30:00+00:00",
        "price": {"latest": 3200, "change_24_bars_pct": -1.2},
        "freshness": {
            "latest_completed_at": "2026-11-03T12:00:00+00:00",
            "stale": stale,
        },
        "market_state": {"regime": "TREND_DOWN", "confidence": 0.7},
        "prediction": {"direction": "DOWN", "model_consensus": "MIXED"},
        "signal": {"research_signal": "NO_ACTION", "order_permission": False},
        "indicators": {"rsi_14_simple": 32.0},
        "observers": {"nemt": {"phase": "contraction"}},
        "models": [
            {"name": "Chronos", "family": "Chronos", "status": "verified"},
            {"name": "DeepSeek", "family": "DeepSeek", "status": "analysis_not_requested"},
        ],
    }


def test_verified_ai_advisory_is_attached_without_permissions() -> None:
    captured: dict = {}

    def analyzer(**kwargs):
        captured.update(kwargs)
        return {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "analysis": {
                "summary": "Bearish evidence is present but model consensus is mixed.",
                "observations": ["Price momentum is negative."],
                "risks": ["The signal can reverse."],
                "data_quality": ["The final forming candle was excluded."],
                "limitations": ["This is not a calibrated probability."],
                "recommended_research": ["Compare the next completed candle."],
            },
        }

    result = attach_deepseek_market_analysis(_snapshot(), analyzer=analyzer)

    assert result["ai_analysis"]["status"] == "verified_advisory"
    assert result["ai_analysis"]["snapshot_completed_at"] == "2026-11-03T12:00:00+00:00"
    assert result["ai_analysis"]["paper_order_permission"] is False
    assert result["ai_analysis"]["live_order_permission"] is False
    assert "candles" not in captured["evidence"]
    assert result["models"][-1]["status"] == "verified_advisory"


def test_missing_provider_is_visible_without_breaking_market_snapshot() -> None:
    def analyzer(**_):
        raise DeepSeekAnalysisError("not_configured")

    result = attach_deepseek_market_analysis(_snapshot(), analyzer=analyzer)

    assert result["symbol"] == "ETH/USDT"
    assert result["ai_analysis"] == {
        "status": "unavailable",
        "reason": "not_configured",
        "advisory_only": True,
        "paper_order_permission": False,
        "live_order_permission": False,
    }


def test_stale_market_data_blocks_ai_call() -> None:
    def analyzer(**_):
        raise AssertionError("stale data must not be sent to DeepSeek")

    result = attach_deepseek_market_analysis(_snapshot(stale=True), analyzer=analyzer)

    assert result["ai_analysis"]["reason"] == "stale_market_data"
    assert result["models"][-1]["status"] == "blocked_stale_data"
