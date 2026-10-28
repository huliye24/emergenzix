"""Immutable registry and provenance helpers for reviewed NEMT strategies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any, Mapping, Sequence

from .nemt_momentum_v2 import MomentumV2Config
from .nemt_pyramiding_v4 import PyramidingV4Config
from .nemt_strategy_v3 import StrategyV3Config

DEFAULT_STRATEGY_ID = "nemt-v4.4-regime-router-monthly-001"
SOURCE_REPOSITORY = "huliye24/nemt-simulator"
SOURCE_COMMIT = "e764247"
VALIDATION_COMMIT = "3de60ef"


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StrategyDefinition:
    """Versioned research strategy definition with fail-closed safety flags."""

    strategy_id: str
    version: str
    router_protocol: str
    variants: tuple[str, ...]
    parameters: Mapping[str, Any]
    parameter_fingerprint: str
    source_repository: str
    source_commit: str
    validation_commit: str
    evidence_fingerprint: str
    execution_mode: str = "BACKTEST_ONLY"
    live_enabled: bool = False


def _parameters() -> dict[str, Any]:
    return {
        "market": {"exchange": "binance", "symbol": "BTC/USDT", "timeframe": "4h"},
        "costs": {"fee_rate_per_side": 0.0005, "slippage_rate_per_side": 0.0002},
        "risk": {"base_leverage": 3, "strong_leverage": 5, "risk_per_trade": 0.02},
        "momentum": asdict(MomentumV2Config()),
        "entry_and_sizing": asdict(StrategyV3Config()),
        "v4a": asdict(PyramidingV4Config(mode="v4_a_breakeven")),
        "v4b": asdict(PyramidingV4Config(mode="v4_b_atr_trailing")),
        "router": {
            "min_history_months": 8,
            "neighbors": 8,
            "conservative_penalty": 0.5,
            "fallback_variant": "v4c",
            "annual_trend_usage": "research_label_only",
            "position_overlap": False,
        },
    }


def _evidence_bytes() -> bytes:
    return files(__package__).joinpath("evidence/v44_monthly_2024_2026.json").read_bytes()


def _build_default() -> StrategyDefinition:
    parameters = _parameters()
    return StrategyDefinition(
        strategy_id=DEFAULT_STRATEGY_ID,
        version="4.4.0",
        router_protocol="nemt-v4.4-regime-router-monthly-001",
        variants=("v4a", "v4b", "v4c"),
        parameters=parameters,
        parameter_fingerprint=_canonical_sha256(parameters),
        source_repository=SOURCE_REPOSITORY,
        source_commit=SOURCE_COMMIT,
        validation_commit=VALIDATION_COMMIT,
        evidence_fingerprint=hashlib.sha256(_evidence_bytes()).hexdigest(),
    )


_STRATEGIES = {DEFAULT_STRATEGY_ID: _build_default()}


def get_strategy(strategy_id: str = DEFAULT_STRATEGY_ID) -> StrategyDefinition:
    """Return a reviewed strategy; unknown IDs fail closed."""
    try:
        return _STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown NEMT strategy: {strategy_id}") from exc


def list_strategies() -> tuple[StrategyDefinition, ...]:
    """Return all reviewed NEMT strategies in stable order."""
    return tuple(_STRATEGIES[key] for key in sorted(_STRATEGIES))


def data_fingerprint(bars: Sequence[Mapping[str, Any]]) -> str:
    """Hash canonical OHLCV content, including timestamp and ordering."""
    canonical = []
    for bar in bars:
        timestamp = bar["timestamp"]
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        canonical.append({
            "timestamp": str(timestamp),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar["volume"]),
        })
    return _canonical_sha256(canonical)


def report_fingerprint(report: Mapping[str, Any]) -> str:
    """Hash a completed report after excluding its self-referential hash."""
    payload = dict(report)
    payload.pop("report_fingerprint", None)
    return _canonical_sha256(payload)
