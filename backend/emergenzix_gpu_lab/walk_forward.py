"""Deterministic, read-only GPU Alpha Lab walk-forward window planning.

This module does not fit a model, compute normalization, score a strategy,
place orders, or write to the registered dataset. Each fold describes a
chronological expanding-history training/validation/holdout experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .dataset import load_registered_dataset

if TYPE_CHECKING:
    from .dataset import ResearchDataset


PROTOCOL = "gpu-walk-forward-plan-v1"


@dataclass(frozen=True)
class Stage:
    """Half-open candle/anchor ranges; targets are inclusive candle indexes."""

    bar_start: int
    bar_stop: int
    anchor_start: int
    anchor_stop: int
    first_target: int
    last_target: int

    @property
    def sample_count(self) -> int:
        return self.anchor_stop - self.anchor_start


@dataclass(frozen=True)
class Fold:
    number: int
    train: Stage
    validation: Stage
    test: Stage


def _positive_int(name: str, value: int, *, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _stage(bar_start: int, bar_stop: int, anchor_start: int,
           horizon: int) -> Stage:
    anchor_stop = bar_stop - horizon
    if anchor_start >= anchor_stop:
        raise ValueError("not enough purged anchors in one fold stage")
    return Stage(
        bar_start=bar_start, bar_stop=bar_stop,
        anchor_start=anchor_start, anchor_stop=anchor_stop,
        first_target=anchor_start + horizon,
        last_target=anchor_stop - 1 + horizon,
    )


def plan_walk_forward(
    candle_count: int,
    *,
    folds: int = 3,
    train_bars: int = 512,
    validation_bars: int = 128,
    test_bars: int = 128,
    step_bars: int | None = None,
    lookback: int = 32,
    horizon: int = 1,
) -> tuple[Fold, ...]:
    """Build expanding train, fixed validation and nonoverlapping test folds.

    Fold i uses train bars [0, train_end), validation
    [train_end, validation_end), test [validation_end, test_end).
    An anchor at t observes only candles <= t; its label is at t+horizon.
    All labels must end *strictly before* the following split starts.
    Next-fold training may use earlier folds' completed historical test periods;
    it may never use its own validation/test periods.
    """
    if step_bars is None:
        step_bars = test_bars
    for name, value, minimum in (
        ("candle_count", candle_count, 1), ("folds", folds, 2),
        ("train_bars", train_bars, 1),
        ("validation_bars", validation_bars, 1),
        ("test_bars", test_bars, 1), ("step_bars", step_bars, 1),
        ("lookback", lookback, 8), ("horizon", horizon, 1),
    ):
        _positive_int(name, value, minimum=minimum)
    if folds > 24 or lookback > 256 or horizon > 24:
        raise ValueError("folds <= 24, lookback <= 256, horizon <= 24 required")
    if train_bars < lookback + horizon + 32:
        raise ValueError("train_bars is too short for 32 purged training examples")
    if validation_bars < horizon + 5 or test_bars < horizon + 5:
        raise ValueError("validation and test need at least five purged examples")
    if step_bars < test_bars:
        raise ValueError("step_bars must be >= test_bars to prevent test overlap")

    minimum_candles = (
        train_bars + validation_bars + test_bars + (folds - 1) * step_bars
    )
    if candle_count < minimum_candles:
        raise ValueError(
            f"insufficient history: {minimum_candles} candles required "
            f"for {folds} walk-forward folds, only {candle_count} available"
        )

    result: list[Fold] = []
    for i in range(folds):
        train_end = train_bars + i * step_bars
        validation_end = train_end + validation_bars
        test_end = validation_end + test_bars
        fold = Fold(
            number=i + 1,
            train=_stage(0, train_end, lookback - 1, horizon),
            validation=_stage(train_end, validation_end, train_end, horizon),
            test=_stage(validation_end, test_end, validation_end, horizon),
        )
        if result and result[-1].test.bar_stop > fold.test.bar_start:
            raise ValueError("walk-forward test windows overlap")
        result.append(fold)
    return tuple(result)


def describe_plan(
    dataset: ResearchDataset, *,
    folds: int = 3,
    train_bars: int = 512,
    validation_bars: int = 128,
    test_bars: int = 128,
    step_bars: int | None = None,
    lookback: int = 32,
    horizon: int = 1,
) -> dict[str, Any]:
    """Bind index-only folds to verified dataset fingerprints/timestamps."""
    windows = plan_walk_forward(
        len(dataset.times), folds=folds, train_bars=train_bars,
        validation_bars=validation_bars, test_bars=test_bars,
        step_bars=step_bars, lookback=lookback, horizon=horizon,
    )
    if len(dataset.times) != len(dataset.close):
        raise ValueError("dataset times and OHLCV lengths disagree")

    def stage_info(stage: Stage) -> dict[str, Any]:
        return {
            "bars": [stage.bar_start, stage.bar_stop],
            "anchors": [stage.anchor_start, stage.anchor_stop],
            "first_target_index": stage.first_target,
            "last_target_index": stage.last_target,
            "sample_count": stage.sample_count,
            "first_anchor_at": dataset.times[stage.anchor_start],
            "last_anchor_at": dataset.times[stage.anchor_stop - 1],
            "first_target_at": dataset.times[stage.first_target],
            "last_target_at": dataset.times[stage.last_target],
        }

    report: dict[str, Any] = {
        "protocol": PROTOCOL,
        "dataset_sha256": dataset.dataset_id,
        "symbol": dataset.symbol,
        "timeframe": dataset.timeframe,
        "candle_count": len(dataset.times),
        "research_only": True,
        "no_order_execution": True,
        "training_performed": False,
        "configuration": {
            "folds": folds,
            "train_bars_initial": train_bars,
            "validation_bars": validation_bars,
            "test_bars": test_bars,
            "step_bars": test_bars if step_bars is None else step_bars,
            "lookback": lookback,
            "horizon": horizon,
        },
        "unused_tail_bars": len(dataset.times) - windows[-1].test.bar_stop,
        "windows": [
            {
                "fold": window.number,
                "train": stage_info(window.train),
                "validation": stage_info(window.validation),
                "test": stage_info(window.test),
            }
            for window in windows
        ],
        "limitations": (
            "Plan only. Fit a NEW per-fold model and feature normalizer on "
            "that fold's training data, select epochs with validation only, "
            "and never select hyperparameters using test returns. "
            "No backtest PnL, slippage, fees or trading permissions."
        ),
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    report["plan_sha256"] = hashlib.sha256(payload).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only verified OHLCV walk-forward planning; no training"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--train-bars", type=int, default=512)
    parser.add_argument("--validation-bars", type=int, default=128)
    parser.add_argument("--test-bars", type=int, default=128)
    parser.add_argument("--step-bars", type=int, default=None)
    parser.add_argument("--lookback", type=int, default=32)
    parser.add_argument("--horizon", type=int, default=1)
    args = parser.parse_args()
    dataset = load_registered_dataset(args.db_path, args.dataset_id)
    result = describe_plan(
        dataset, folds=args.folds, train_bars=args.train_bars,
        validation_bars=args.validation_bars, test_bars=args.test_bars,
        step_bars=args.step_bars, lookback=args.lookback,
        horizon=args.horizon,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
