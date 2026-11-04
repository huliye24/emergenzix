"""Deterministic first-version market microstructure state estimation."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .strategy_evidence_pipeline import canonical_sha256


SCHEMA_VERSION = "emergenzix-market-battlefield/v1"


def build_market_battlefield(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one bounded futures snapshot into descriptive, non-executable state."""
    bids = _levels(snapshot.get("bids"), descending=True)
    asks = _levels(snapshot.get("asks"), descending=False)
    trades = snapshot.get("trades")
    derivatives = snapshot.get("derivatives")
    if not bids or not asks or not isinstance(trades, list) or not isinstance(derivatives, Mapping):
        raise ValueError("battlefield snapshot is incomplete")
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        raise ValueError("order book is crossed")
    mid = (best_bid + best_ask) / 2.0
    buy_notional = 0.0
    sell_notional = 0.0
    for trade in trades:
        if not isinstance(trade, Mapping):
            raise ValueError("trade rows must be objects")
        price, quantity = _positive(trade.get("price")), _positive(trade.get("quantity"))
        side = trade.get("aggressor_side")
        if side == "BUY":
            buy_notional += price * quantity
        elif side == "SELL":
            sell_notional += price * quantity
        else:
            raise ValueError("trade aggressor side is invalid")
    total_notional = buy_notional + sell_notional
    if total_notional <= 0:
        raise ValueError("trade notional must be positive")
    signed_flow = buy_notional - sell_notional
    flow_imbalance = signed_flow / total_notional
    bid_depth = _band_notional(bids, mid=mid, lower=mid * 0.99, upper=mid)
    ask_depth = _band_notional(asks, mid=mid, lower=mid, upper=mid * 1.01)
    depth_total = bid_depth + ask_depth
    if depth_total <= 0:
        raise ValueError("one-percent order-book depth is empty")
    depth_imbalance = (bid_depth - ask_depth) / depth_total
    support = _wall(bids, mid=mid, side="BID")
    resistance = _wall(asks, mid=mid, side="ASK")
    oi_now = _positive(derivatives.get("open_interest"))
    oi_previous = _positive(derivatives.get("open_interest_previous"))
    oi_change = (oi_now / oi_previous) - 1.0
    funding_rate = float(derivatives.get("funding_rate"))
    if not math.isfinite(funding_rate):
        raise ValueError("funding rate must be finite")
    direction = "BUY" if flow_imbalance > 0.05 else "SELL" if flow_imbalance < -0.05 else "BALANCED"
    pressure = "BID" if depth_imbalance > 0.1 else "ASK" if depth_imbalance < -0.1 else "BALANCED"
    agreement = (
        "CONFIRMED" if (direction == "BUY" and pressure == "BID") or
        (direction == "SELL" and pressure == "ASK") else
        "CONFLICT" if direction != "BALANCED" and pressure not in {"BALANCED", direction.replace("BUY", "BID").replace("SELL", "ASK")} else
        "MIXED"
    )
    state = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": str(snapshot.get("observed_at", "")),
        "symbol": str(snapshot.get("symbol", "")),
        "mid_price": mid,
        "flow": {
            "direction": direction,
            "buy_notional": buy_notional,
            "sell_notional": sell_notional,
            "signed_notional": signed_flow,
            "imbalance": flow_imbalance,
            "sample_trades": len(trades),
        },
        "liquidity": {
            "pressure": pressure,
            "bid_depth_1pct": bid_depth,
            "ask_depth_1pct": ask_depth,
            "depth_imbalance": depth_imbalance,
            "support": support,
            "resistance": resistance,
        },
        "derivatives": {
            "open_interest": oi_now,
            "open_interest_change": oi_change,
            "funding_rate": funding_rate,
        },
        "state": {
            "flow_depth_agreement": agreement,
            "label": _state_label(direction, pressure, oi_change),
        },
        "limitations": [
            "Support and resistance are potential visible walls, not verified persistent liquidity.",
            "A single snapshot cannot measure refill, cancellation hazard, or absorption.",
            "Open-interest change does not identify the direction of newly opened positions.",
        ],
        "advisory_only": True,
        "order_permission": False,
    }
    state["snapshot_sha256"] = canonical_sha256(snapshot)
    return state


def _levels(value: object, *, descending: bool) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        raise ValueError("order-book levels must be a list")
    levels = [(_positive(item[0]), _positive(item[1])) for item in value
              if isinstance(item, (list, tuple)) and len(item) >= 2]
    if len(levels) != len(value):
        raise ValueError("order-book level is malformed")
    return sorted(levels, key=lambda item: item[0], reverse=descending)


def _band_notional(levels: list[tuple[float, float]], *, mid: float,
                   lower: float, upper: float) -> float:
    del mid
    return sum(price * quantity for price, quantity in levels if lower <= price <= upper)


def _wall(levels: list[tuple[float, float]], *, mid: float, side: str) -> dict[str, Any]:
    candidates = [(price, price * quantity) for price, quantity in levels
                  if abs(price / mid - 1.0) <= 0.02]
    if not candidates:
        raise ValueError(f"no {side.lower()} wall inside two percent")
    price, notional = max(candidates, key=lambda item: item[1])
    return {"price": price, "notional": notional, "distance_pct": abs(price / mid - 1.0),
            "verification": "VISIBLE_SNAPSHOT_ONLY"}


def _state_label(direction: str, pressure: str, oi_change: float) -> str:
    if direction == "BUY" and pressure == "BID":
        return "BUY_FLOW_WITH_BID_SUPPORT_OI_RISING" if oi_change > 0 else "BUY_FLOW_WITH_BID_SUPPORT"
    if direction == "SELL" and pressure == "ASK":
        return "SELL_FLOW_WITH_ASK_RESISTANCE_OI_RISING" if oi_change > 0 else "SELL_FLOW_WITH_ASK_RESISTANCE"
    if direction == "BUY" and pressure == "ASK":
        return "BUY_FLOW_FACING_VISIBLE_RESISTANCE"
    if direction == "SELL" and pressure == "BID":
        return "SELL_FLOW_FACING_VISIBLE_SUPPORT"
    return "BALANCED_OR_MIXED"


def _positive(value: object) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("market values must be finite and positive")
    return parsed
