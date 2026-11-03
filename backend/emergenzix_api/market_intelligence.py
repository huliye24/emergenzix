"""Read-only market observation assembled from public OHLCV candles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .model_signal_artifacts import load_verified_model_signals
from .nemt_phase_observer import observe_nemt_phase
from .scenarios import analyze_scenarios
from .schemas import Bar


TIMEFRAME_SECONDS = {"15m": 900, "1h": 3_600, "4h": 14_400}


def build_market_intelligence(
    market_data: Mapping[str, Any], *, now: datetime | None = None,
    model_signal_artifact_path: str | None = None,
) -> dict[str, Any]:
    """Create an advisory snapshot; it never grants order permission."""
    timeframe = str(market_data.get("timeframe", ""))
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("market intelligence supports 15m, 1h, and 4h")

    raw_bars = market_data.get("bars")
    if not isinstance(raw_bars, list) or len(raw_bars) < 25:
        raise ValueError("at least 25 ordered OHLCV bars are required")
    bars = [Bar.model_validate(item) for item in raw_bars]
    if any(current.timestamp <= previous.timestamp for previous, current in zip(bars, bars[1:])):
        raise ValueError("OHLCV bars must be strictly ordered and unique")

    # Public exchange endpoints commonly include a still-forming final candle.
    # It may be displayed as the observed price but never enters the classifier.
    completed = bars[:-1]
    scenario = analyze_scenarios(completed, lookback=20)
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    latest_completed = completed[-1]
    age_seconds = max(0, int((observed_at - latest_completed.timestamp).total_seconds()))
    interval_seconds = TIMEFRAME_SECONDS[timeframe]
    stale = age_seconds > interval_seconds * 3

    latest = bars[-1]
    comparison_index = max(0, len(bars) - 25)
    change_24_bars_pct = (latest.close / bars[comparison_index].close - 1.0) * 100
    atr_pct = scenario.atr / latest_completed.close * 100

    regime_map = {
        "continuation_down": ("TREND_DOWN", "DOWN", "NO_ACTION", None),
        "false_breakdown": ("RECOVERY", "UP", "WATCH_LONG", "TURTLE_20_10"),
        "range": ("RANGE", "NEUTRAL", "NO_ACTION", None),
        "reversal_up": ("TREND_UP", "UP", "WATCH_LONG", "TURTLE_20_10"),
    }
    regime, direction, research_signal, candidate = regime_map[scenario.scenario]
    if stale:
        research_signal = "NO_ACTION"
        candidate = None
    nemt_observation = observe_nemt_phase(completed)
    verified_models = load_verified_model_signals(
        model_signal_artifact_path,
        bars=completed,
        symbol=str(market_data.get("symbol")),
        timeframe=timeframe,
        now=observed_at,
    )
    accepted_directions = [
        str(item["direction"])
        for item in verified_models
        if item.get("status") == "verified"
    ]
    model_consensus = (
        accepted_directions[0]
        if accepted_directions and len(set(accepted_directions)) == 1
        else "MIXED" if accepted_directions else "UNAVAILABLE"
    )

    return {
        "source": market_data.get("source", "ccxt_public_ohlcv"),
        "exchange": market_data.get("exchange", "binance"),
        "symbol": market_data.get("symbol"),
        "timeframe": timeframe,
        "observed_at": observed_at.isoformat(),
        "price": {
            "latest": latest.close,
            "latest_bar_timestamp": latest.timestamp.isoformat(),
            "change_24_bars_pct": round(change_24_bars_pct, 4),
        },
        "freshness": {
            "latest_completed_at": latest_completed.timestamp.isoformat(),
            "age_seconds": age_seconds,
            "stale": stale,
            "forming_bar_excluded": True,
        },
        "market_state": {
            "regime": regime,
            "scenario": scenario.scenario,
            "confidence": scenario.confidence,
            "atr_pct": round(atr_pct, 4),
            "support": scenario.support,
            "resistance": scenario.resistance,
        },
        "observers": {
            "scenario_baseline": scenario.as_dict(),
            "nemt": nemt_observation,
        },
        "prediction": {
            "method": "deterministic_scenario_baseline",
            "direction": direction,
            "score": scenario.confidence,
            "horizon_bars": 1,
            "calibrated_probability": False,
            "model_consensus": model_consensus,
            "verified_model_count": len(accepted_directions),
        },
        "signal": {
            "research_signal": research_signal,
            "strategy_candidate": candidate,
            "reason": "stale_market_data" if stale else scenario.trigger,
            "invalidation": scenario.invalidation,
            "order_permission": False,
        },
        "models": verified_models + [
            {"name": "DeepSeek", "family": "DeepSeek", "status": "analysis_not_requested", "order_permission": False},
        ],
        "series": [
            {"timestamp": bar.timestamp.isoformat(), "close": bar.close}
            for bar in bars[-80:]
        ],
        "safety": {
            "advisory_only": True,
            "execution_decision": "NO_ACTION",
            "paper_order_permission": False,
            "live_order_permission": False,
            "live_trading_enabled": False,
        },
        "limitations": [
            "The direction score is a heuristic baseline, not a calibrated forecast probability.",
            "Chronos and Qlib outputs are used only when their versions, input snapshot, and timestamps verify.",
            "DeepSeek analysis is not included in this deterministic snapshot.",
            "A research signal cannot authorize PAPER or LIVE execution.",
        ],
    }
