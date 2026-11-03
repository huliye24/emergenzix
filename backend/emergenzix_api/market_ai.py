"""Bounded DeepSeek advisory enrichment for deterministic market snapshots."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .deepseek_analysis import DeepSeekAnalysisError, analyze_research_evidence


MarketAnalyzer = Callable[..., dict[str, Any]]


def attach_deepseek_market_analysis(
    snapshot: Mapping[str, Any],
    *,
    analyzer: MarketAnalyzer = analyze_research_evidence,
) -> dict[str, Any]:
    """Attach a read-only AI review without changing trusted signals or permissions."""
    enriched = dict(snapshot)
    freshness = snapshot.get("freshness", {})
    if not isinstance(freshness, Mapping) or freshness.get("stale") is True:
        enriched["ai_analysis"] = _unavailable("stale_market_data")
        return _replace_deepseek_model(enriched, "blocked_stale_data")

    evidence = {
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "observed_at": snapshot.get("observed_at"),
        "latest_completed_at": freshness.get("latest_completed_at"),
        "price": snapshot.get("price"),
        "market_state": snapshot.get("market_state"),
        "prediction": snapshot.get("prediction"),
        "signal": snapshot.get("signal"),
        "indicators": snapshot.get("indicators"),
        "observers": snapshot.get("observers"),
    }
    try:
        result = analyzer(
            title=f"{snapshot.get('symbol')} {snapshot.get('timeframe')} market observation",
            question=(
                "Summarize the current market regime, agreement or conflict among the "
                "provided indicators, major uncertainty and invalidation risks, and the "
                "next research checks. This is observation only; do not recommend or "
                "authorize an order."
            ),
            evidence=evidence,
        )
    except DeepSeekAnalysisError as exc:
        reason = (
            exc.reason
            if exc.reason in {"not_configured", "invalid_configuration", "unsupported_model"}
            else "provider_unavailable"
        )
        enriched["ai_analysis"] = _unavailable(reason)
        return _replace_deepseek_model(enriched, "unavailable")

    enriched["ai_analysis"] = {
        "status": "verified_advisory",
        "provider": result["provider"],
        "model": result["model"],
        "snapshot_completed_at": freshness.get("latest_completed_at"),
        "analysis": result["analysis"],
        "advisory_only": True,
        "paper_order_permission": False,
        "live_order_permission": False,
    }
    return _replace_deepseek_model(enriched, "verified_advisory")


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "advisory_only": True,
        "paper_order_permission": False,
        "live_order_permission": False,
    }


def _replace_deepseek_model(snapshot: dict[str, Any], status: str) -> dict[str, Any]:
    models = [
        item
        for item in snapshot.get("models", [])
        if not isinstance(item, Mapping) or item.get("family") != "DeepSeek"
    ]
    models.append(
        {
            "name": "DeepSeek",
            "family": "DeepSeek",
            "status": status,
            "order_permission": False,
        }
    )
    snapshot["models"] = models
    return snapshot
