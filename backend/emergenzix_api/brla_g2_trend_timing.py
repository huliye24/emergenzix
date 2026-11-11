"""BRLA-G2 v2 trend maturity, entry timing, and action-specific objectives."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .brla_g2_multitimeframe import BASE_INTERVAL, MultiTimeframeObservation, build_multitimeframe_observation
from .brla_g2_order_labels import CompetitionLabel, FUTURE_BAR_COUNT, OldDirection
from .brla_g2_stage1_dataset import Stage1Dataset, Stage1Example, build_stage1_dataset
from .brla_g2_training_protocol import DataPurpose, ObjectiveAudit, stage2_reward
from .brmg_scenarios import Partition

PROTOCOL = "emergenzix-brla-g2-trend-timing-v2"
DATASET_PROTOCOL = "emergenzix-brla-g2-stage1-timing-dataset-v2"


@dataclass(frozen=True, slots=True)
class TrendTimingLabel:
    trend_maturity: float
    remaining_potential: float
    entry_location_quality: float
    feedback_exhaustion: float
    late_entry_risk: float
    wave_age: float
    extension: float
    label_sha256: str


@dataclass(frozen=True, slots=True)
class TrendTimingPrediction:
    trend_maturity: float
    remaining_potential: float
    entry_location_quality: float
    feedback_exhaustion: float
    late_entry_risk: float
    position_permission: bool = False
    order_permission: bool = False

    def __post_init__(self) -> None:
        _unit((self.trend_maturity, self.remaining_potential,
               self.entry_location_quality, self.feedback_exhaustion,
               self.late_entry_risk))
        if self.position_permission or self.order_permission:
            raise ValueError("timing prediction has no position or order authority")


@dataclass(frozen=True, slots=True)
class TimingStage1Example:
    base_example: Stage1Example
    timing_label: TrendTimingLabel
    example_sha256: str
    future_raw_bars_included: bool = False
    position_permission: bool = False
    order_permission: bool = False


@dataclass(frozen=True, slots=True)
class TimingStage1Dataset:
    protocol: str
    base_dataset_sha256: str
    partition: Partition
    purpose: DataPurpose
    examples: tuple[TimingStage1Example, ...]
    dataset_sha256: str
    automatic_promotion: bool = False
    position_permission: bool = False
    order_permission: bool = False


def _unit(values) -> None:
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError("timing values must be finite and in [0, 1]")


def _clip(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(min(value, 30), -30)))


def label_trend_timing(observation: MultiTimeframeObservation,
                       future_bars: Sequence[Mapping[str, Any]],
                       competition: CompetitionLabel) -> TrendTimingLabel:
    if len(future_bars) != FUTURE_BAR_COUNT or competition.scenario_sha256 == "":
        raise ValueError("timing labels require the frozen seven-day future window")
    closes = [bar.close for bar in observation.windows["4h"].bars[-60:]]
    current = closes[-1]
    bearish = competition.old_direction is OldDirection.BEARISH
    pivot = closes.index(max(closes) if bearish else min(closes))
    wave_bars = max(len(closes) - 1 - pivot, 1)
    wave_age = _clip(wave_bars / (len(closes) - 1))
    returns = [math.log(closes[index] / closes[index - 1])
               for index in range(1, len(closes))]
    scale = (statistics.pstdev(returns) or 1e-9) * math.sqrt(wave_bars)
    directional_extension = (math.log(closes[pivot] / current) if bearish
                             else math.log(current / closes[pivot]))
    extension = _clip(_sigmoid(directional_extension / scale - 1))
    future_closes = [float(row["close"]) for row in future_bars]
    favorable = ((current - min(future_closes)) / current if bearish
                 else (max(future_closes) - current) / current)
    future_returns = [math.log(future_closes[index] / future_closes[index - 1])
                      for index in range(1, len(future_closes))]
    future_scale = (statistics.pstdev(future_returns) or 1e-9) * math.sqrt(len(future_returns))
    remaining = _clip(_sigmoid(favorable / max(future_scale, 1e-9) - .75))
    exhaustion = _clip(.35 * wave_age + .25 * competition.new_order_strength
                       + .25 * (1 - remaining) + .15 * competition.challenger_persistence)
    maturity = _clip(.45 * wave_age + .25 * extension + .30 * (1 - remaining))
    ideal = math.exp(-((maturity - .35) / .24) ** 2)
    entry_quality = _clip(ideal * remaining * (1 - .5 * exhaustion))
    late_risk = _clip(.65 * _clip((maturity - .55) / .45) + .35 * exhaustion)
    values = (maturity, remaining, entry_quality, exhaustion, late_risk, wave_age, extension)
    _unit(values)
    payload = {"protocol": PROTOCOL, "competition_label_sha256": competition.label_sha256,
        "trend_maturity": maturity, "remaining_potential": remaining,
        "entry_location_quality": entry_quality, "feedback_exhaustion": exhaustion,
        "late_entry_risk": late_risk, "wave_age": wave_age, "extension": extension,
        "position_permission": False, "order_permission": False}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return TrendTimingLabel(*values, digest)


def timing_loss(prediction: TrendTimingPrediction,
                label: TrendTimingLabel) -> ObjectiveAudit:
    components = {
        "trend_maturity_mse": (prediction.trend_maturity - label.trend_maturity) ** 2,
        "remaining_potential_mse": (prediction.remaining_potential
                                    - label.remaining_potential) ** 2,
        "entry_location_mse": (prediction.entry_location_quality
                               - label.entry_location_quality) ** 2,
        "feedback_exhaustion_mse": .75 * (prediction.feedback_exhaustion
                                          - label.feedback_exhaustion) ** 2,
        "late_entry_risk_mse": (prediction.late_entry_risk - label.late_entry_risk) ** 2,
    }
    return _objective("MINIMIZE_STAGE1_TIMING_LOSS", components)


def stage2_timing_reward(*, trend_maturity: float, remaining_potential: float,
                         entry_location_quality: float, feedback_exhaustion: float,
                         late_entry_risk: float, new_entry: bool, hold_existing: bool,
                         add_risk: bool, reduce: bool, exit_position: bool,
                         unrealized_profit: float, old_order_invalidated: bool,
                         stop_tightened: bool, **base_reward_inputs) -> ObjectiveAudit:
    _unit((trend_maturity, remaining_potential, entry_location_quality,
           feedback_exhaustion, late_entry_risk))
    actions = (new_entry, hold_existing, add_risk, reduce, exit_position)
    if sum(map(int, actions)) != 1:
        raise ValueError("exactly one timing action must be identified")
    base = stage2_reward(**base_reward_inputs)
    components = dict(base.components)
    components.update({
        "early_quality_entry": .004 * entry_location_quality
            if new_entry and .15 <= trend_maturity <= .55 else 0.0,
        "late_new_entry": -.03 * late_entry_risk if new_entry else 0.0,
        "mature_add": -.05 * late_entry_risk if add_risk and trend_maturity >= .70 else 0.0,
        "profitable_mature_hold": .003 if hold_existing and unrealized_profit > 0
            and trend_maturity >= .50 else 0.0,
        "protective_stop_tightening": .002 if hold_existing and stop_tightened else 0.0,
        "premature_profitable_exit": -.01 * remaining_potential
            if exit_position and unrealized_profit > 0 and not old_order_invalidated else 0.0,
        "timely_exhaustion_exit": .005 if exit_position
            and (old_order_invalidated or feedback_exhaustion >= .70) else 0.0,
    })
    return _objective("MAXIMIZE_STAGE2_TIMING_REWARD", components)


def _objective(name: str, components: Mapping[str, float]) -> ObjectiveAudit:
    total = math.fsum(components.values())
    payload = {"protocol": PROTOCOL, "objective": name,
               "components": dict(components), "total": total,
               "automatic_promotion": False, "order_permission": False}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return ObjectiveAudit(PROTOCOL, name, dict(components), total, digest)


def build_stage1_timing_dataset(source_bars: Sequence[Mapping[str, Any]], *,
                                scenario_sha256: str, partition: Partition,
                                purpose: DataPurpose, stride_bars: int = 96,
                                model_frozen: bool = False,
                                sealed_access_authorized: bool = False
                                ) -> TimingStage1Dataset:
    base = build_stage1_dataset(source_bars, scenario_sha256=scenario_sha256,
        partition=partition, purpose=purpose, stride_bars=stride_bars,
        model_frozen=model_frozen, sealed_access_authorized=sealed_access_authorized)
    rows = []
    for example in base.examples:
        as_of = datetime.fromisoformat(example.as_of)
        observation = build_multitimeframe_observation(source_bars, as_of=as_of)
        future = [row for row in source_bars if as_of <= datetime.fromisoformat(
            str(row["timestamp"]).replace("Z", "+00:00"))
            < as_of + BASE_INTERVAL * FUTURE_BAR_COUNT]
        timing = label_trend_timing(observation, future, example.label)
        payload = {"base_example_sha256": example.example_sha256,
                   "timing_label": asdict(timing), "future_raw_bars_included": False,
                   "position_permission": False, "order_permission": False}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        rows.append(TimingStage1Example(example, timing, digest))
    hashes = tuple(row.example_sha256 for row in rows)
    identity = {"protocol": DATASET_PROTOCOL, "base_dataset_sha256": base.dataset_sha256,
        "partition": partition.value, "purpose": purpose.value, "examples": hashes,
        "automatic_promotion": False, "position_permission": False, "order_permission": False}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return TimingStage1Dataset(DATASET_PROTOCOL, base.dataset_sha256, partition,
                               purpose, tuple(rows), digest)


def timing_protocol_manifest() -> dict[str, Any]:
    payload = {"protocol": PROTOCOL, "dataset_protocol": DATASET_PROTOCOL,
        "stage1_targets": ("trend_maturity", "remaining_potential",
            "entry_location_quality", "feedback_exhaustion", "late_entry_risk"),
        "mature_new_risk_threshold": .70,
        "principle": "DIRECTION_SELECTS_ATTENTION_TIMING_SELECTS_ENTRY",
        "position_permission": False, "order_permission": False,
        "automatic_promotion": False}
    payload["protocol_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return payload
