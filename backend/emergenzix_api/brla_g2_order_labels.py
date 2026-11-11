"""Continuous old-order versus challenger-order teacher labels for BRLA-G2."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .brla_g2_multitimeframe import BASE_INTERVAL, MultiTimeframeObservation
from .brla_g2_training_protocol import OrderState, Stage1Label
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-continuous-order-label-v1"
FUTURE_BAR_COUNT = 7 * 24 * 4
CHALLENGER_FLOOR = .02


class OldDirection(StrEnum):
    BEARISH = "BEARISH"
    BULLISH = "BULLISH"


@dataclass(frozen=True, slots=True)
class CompetitionLabel:
    protocol: str
    scenario_sha256: str
    partition: Partition
    as_of: str
    label_horizon_end: str
    old_direction: OldDirection
    old_order_strength: float
    new_order_strength: float
    dominance_gap: float
    challenger_persistence: float
    dominance_velocity: float
    confidence: float
    state: OrderState
    components: Mapping[str, float]
    future_window_sha256: str
    label_sha256: str
    generated_with_future_outcomes: bool = True
    mode: str = "RESEARCH"
    position_permission: bool = False
    order_permission: bool = False


def _clip(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(min(value, 30), -30)))


def _parse(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _returns(closes: Sequence[float]) -> list[float]:
    return [math.log(closes[index] / closes[index - 1])
            for index in range(1, len(closes))]


def _trend_evidence(closes: Sequence[float]) -> tuple[int, float, float]:
    returns = _returns(closes)
    total = math.log(closes[-1] / closes[0])
    volatility = statistics.pstdev(returns) or 1e-9
    direction = 1 if total >= 0 else -1
    signal = abs(total) / (volatility * math.sqrt(len(returns)))
    efficiency = abs(closes[-1] - closes[0]) / max(
        sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes))), 1e-9)
    return direction, _clip(_sigmoid(signal - .75)), _clip(efficiency)


def _future_scores(future_closes: Sequence[float], *, old_sign: int,
                   past_reference_low: float, past_reference_high: float
                   ) -> dict[str, float]:
    returns = _returns(future_closes)
    volatility = statistics.pstdev(returns) or 1e-9
    scale = volatility * math.sqrt(len(returns))
    total = math.log(future_closes[-1] / future_closes[0])
    aligned = old_sign * total / scale
    old_continuation = _clip(_sigmoid(aligned))
    challenger_return = _clip(_sigmoid(-aligned))
    if old_sign < 0:
        structure_break = _clip((max(future_closes) / past_reference_high - 1) / max(scale, 1e-9))
        old_extension = _clip((past_reference_low / min(future_closes) - 1) / max(scale, 1e-9))
        counter_peak = max(future_closes)
        challenger_hold = _clip((future_closes[-1] / future_closes[0] - 1)
                                / max(counter_peak / future_closes[0] - 1, 1e-9))
    else:
        structure_break = _clip((past_reference_low / min(future_closes) - 1) / max(scale, 1e-9))
        old_extension = _clip((max(future_closes) / past_reference_high - 1) / max(scale, 1e-9))
        counter_peak = min(future_closes)
        challenger_hold = _clip((1 - future_closes[-1] / future_closes[0])
                                / max(1 - counter_peak / future_closes[0], 1e-9))
    block = max(4, len(future_closes) // 14)
    block_moves = []
    for start in range(0, len(future_closes) - block, block):
        move = math.log(future_closes[start + block] / future_closes[start])
        block_moves.append(-old_sign * move)
    challenger_persistence = sum(move > 0 for move in block_moves) / max(len(block_moves), 1)
    midpoint = len(future_closes) // 2
    first_gap = -old_sign * math.log(future_closes[midpoint] / future_closes[0])
    second_gap = -old_sign * math.log(future_closes[-1] / future_closes[midpoint])
    velocity = max(min((second_gap - first_gap) / max(scale, 1e-9), 1.0), -1.0)
    return {"old_continuation": old_continuation,
            "challenger_return": challenger_return,
            "structure_break": structure_break, "old_extension": old_extension,
            "challenger_hold": challenger_hold,
            "challenger_persistence": challenger_persistence,
            "dominance_velocity": velocity}


def _state(old_strength: float, new_strength: float, persistence: float,
           velocity: float, structure_break: float) -> OrderState:
    gap = new_strength - old_strength
    if gap >= .25 and persistence >= .60 and structure_break >= .35:
        return OrderState.NEW_ORDER_CONFIRMED
    if gap >= .12:
        return OrderState.NEW_ORDER_PROVISIONAL
    if old_strength < .45 and new_strength >= .45 and structure_break >= .20:
        return OrderState.OLD_ORDER_FAILURE
    if abs(gap) < .12:
        return OrderState.ORDER_CONTEST
    if gap < -.12 and (velocity > .05 or new_strength >= .30):
        return OrderState.NEW_ORDER_EMERGING
    return OrderState.OLD_ORDER_DOMINANT


def label_order_competition(observation: MultiTimeframeObservation,
                            future_bars: Sequence[Mapping[str, Any]], *,
                            scenario_sha256: str,
                            partition: Partition) -> CompetitionLabel:
    if (observation.future_fields_present or not observation.causal
            or len(scenario_sha256) != 64):
        raise ValueError("a verified causal observation and scenario hash are required")
    as_of = _parse(observation.as_of)
    if len(future_bars) != FUTURE_BAR_COUNT:
        raise ValueError("the frozen label horizon requires exactly seven days of 15m bars")
    canonical = []
    for index, row in enumerate(future_bars):
        timestamp = _parse(row["timestamp"])
        expected = as_of + BASE_INTERVAL * index
        values = {name: float(row[name]) for name in
                  ("open", "high", "low", "close", "base_volume")}
        if (timestamp != expected or row.get("is_closed") is not True
                or row.get("is_imputed") is True
                or any(not math.isfinite(value) or value <= 0 for value in values.values())
                or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"])):
            raise ValueError("future label bars must be complete, consecutive and valid")
        canonical.append({"timestamp": timestamp.isoformat(), **values})
    future_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()

    daily_closes = [bar.close for bar in observation.windows["1d"].bars]
    four_hour_closes = [bar.close for bar in observation.windows["4h"].bars]
    daily_direction, daily_strength, daily_efficiency = _trend_evidence(daily_closes)
    four_direction, four_strength, four_efficiency = _trend_evidence(four_hour_closes)
    direction_score = daily_direction * (.65 * daily_strength + .35 * daily_efficiency)
    direction_score += four_direction * (.35 * four_strength + .15 * four_efficiency)
    old_sign = 1 if direction_score >= 0 else -1
    direction = OldDirection.BULLISH if old_sign > 0 else OldDirection.BEARISH
    historical_strength = _clip(.45 * daily_strength + .20 * daily_efficiency
                                + .25 * four_strength + .10 * four_efficiency)
    future_closes = [canonical[0]["open"], *(row["close"] for row in canonical)]
    scores = _future_scores(future_closes, old_sign=old_sign,
        past_reference_low=min(four_hour_closes[-42:]),
        past_reference_high=max(four_hour_closes[-42:]))
    old_strength = _clip(.60 * historical_strength
                         + .25 * scores["old_continuation"]
                         + .15 * scores["old_extension"])
    new_strength = max(CHALLENGER_FLOOR, _clip(
        .05 + .25 * scores["challenger_return"]
        + .25 * scores["structure_break"]
        + .20 * scores["challenger_hold"]
        + .25 * scores["challenger_persistence"]))
    gap = new_strength - old_strength
    state = _state(old_strength, new_strength, scores["challenger_persistence"],
                   scores["dominance_velocity"], scores["structure_break"])
    agreement = 1 - min(abs(scores["challenger_return"]
                            - scores["challenger_persistence"]), 1.0)
    confidence = _clip(.35 + .30 * abs(gap) + .20 * agreement
                       + .15 * max(historical_strength, scores["structure_break"]))
    components = {"historical_old_strength": historical_strength, **scores}
    horizon_end = as_of + BASE_INTERVAL * FUTURE_BAR_COUNT
    base = {"protocol": PROTOCOL, "scenario_sha256": scenario_sha256,
        "partition": partition.value, "as_of": as_of.isoformat(),
        "label_horizon_end": horizon_end.isoformat(), "old_direction": direction.value,
        "old_order_strength": old_strength, "new_order_strength": new_strength,
        "dominance_gap": gap, "challenger_persistence": scores["challenger_persistence"],
        "dominance_velocity": scores["dominance_velocity"], "confidence": confidence,
        "state": state.value, "components": components,
        "future_window_sha256": future_hash, "generated_with_future_outcomes": True,
        "mode": "RESEARCH", "position_permission": False, "order_permission": False}
    label_hash = hashlib.sha256(json.dumps(base, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return CompetitionLabel(PROTOCOL, scenario_sha256, partition, as_of.isoformat(),
        horizon_end.isoformat(), direction, old_strength, new_strength, gap,
        scores["challenger_persistence"], scores["dominance_velocity"], confidence,
        state, components, future_hash, label_hash)


def as_stage1_label(label: CompetitionLabel) -> Stage1Label:
    return Stage1Label(as_of=_parse(label.as_of),
        label_horizon_end=_parse(label.label_horizon_end),
        old_order_strength=label.old_order_strength,
        new_order_strength=label.new_order_strength,
        state=label.state, confidence=label.confidence)


def label_protocol_manifest() -> dict[str, Any]:
    payload = {"protocol": PROTOCOL, "future_interval": "15m",
        "future_bar_count": FUTURE_BAR_COUNT, "future_days": 7,
        "challenger_floor": CHALLENGER_FLOOR,
        "old_direction_source": "PAST_1D_AND_4H_ONLY",
        "future_use": "STAGE1_LABEL_ONLY", "position_permission": False,
        "order_permission": False, "automatic_promotion": False}
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
