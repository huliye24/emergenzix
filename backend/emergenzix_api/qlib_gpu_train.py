"""Train pinned Qlib GPU models and extend the offline Chronos report.

This command is a research-only batch job.  It is not imported by the API and
cannot place orders, promote strategies, or access exchange credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .chronos_walk_forward import summarize_predictions
from .qlib_gpu_benchmark import (
    FEATURE_NAMES,
    SEQUENCE_LENGTH,
    TEST_START,
    TRAIN_END,
    VALID_END,
    VALID_START,
    PreparedFrames,
    SampleKey,
    _collection_sha256,
    prepare_frames,
    validate_prediction_values,
)


MODEL_NAMES = ("transformer", "lstm", "alstm", "tra")
SEED = 20261103


class FrameDataset:
    """Minimal Qlib DatasetH-compatible view over locked pandas frames."""

    def __init__(self, frames: PreparedFrames):
        self._segments = {
            "train": self._group_columns(frames.train),
            "valid": self._group_columns(frames.valid),
            "test": self._group_columns(frames.test),
        }

    @staticmethod
    def _group_columns(frame: pd.DataFrame) -> pd.DataFrame:
        return pd.concat(
            {
                "feature": frame.drop(columns="LABEL0"),
                "label": frame[["LABEL0"]],
            },
            axis=1,
        )

    def prepare(self, segment, col_set=None, data_key=None):  # noqa: ARG002
        if isinstance(segment, (list, tuple)):
            return [self.prepare(name, col_set=col_set, data_key=data_key) for name in segment]
        if segment not in self._segments:
            raise ValueError(f"unknown segment: {segment}")
        frame = self._segments[segment]
        if col_set is None:
            return frame
        if isinstance(col_set, str):
            if col_set not in {"feature", "label"}:
                raise ValueError(f"unknown column set: {col_set}")
            return frame[col_set]
        unknown = set(col_set).difference({"feature", "label"})
        if unknown:
            raise ValueError(f"unknown column sets: {sorted(unknown)}")
        return frame.loc[:, list(col_set)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configure_torch() -> dict[str, str | bool]:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the Qlib GPU benchmark")
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)
    return {
        "torch": torch.__version__,
        "cuda_available": True,
        "device": torch.cuda.get_device_name(0),
    }


def _standard_model(name: str):
    common = {
        "d_feat": len(FEATURE_NAMES),
        "batch_size": 512,
        "n_epochs": 12,
        "lr": 0.001,
        "early_stop": 3,
        "GPU": 0,
        "seed": SEED,
    }
    if name == "transformer":
        from qlib.contrib.model.pytorch_transformer import TransformerModel

        return TransformerModel(
            d_model=32,
            nhead=4,
            num_layers=2,
            dropout=0.1,
            n_jobs=0,
            **common,
        )
    if name == "lstm":
        from qlib.contrib.model.pytorch_lstm import LSTM

        return LSTM(hidden_size=64, num_layers=2, dropout=0.1, **common)
    if name == "alstm":
        from qlib.contrib.model.pytorch_alstm import ALSTM

        return ALSTM(hidden_size=64, num_layers=2, dropout=0.1, **common)
    raise ValueError(f"unsupported standard model: {name}")


def _train_standard(
    name: str, frames: PreparedFrames, artifacts: Path
) -> tuple[pd.Series, dict[str, Any]]:
    model = _standard_model(name)
    evaluation: dict[str, Any] = {}
    model_path = artifacts / f"qlib-{name}.pt"
    model.fit(
        FrameDataset(frames),
        evals_result=evaluation,
        save_path=str(model_path),
    )
    prediction = model.predict(FrameDataset(frames), segment="test")
    if not isinstance(prediction, pd.Series):
        prediction = pd.Series(prediction)
    return prediction.astype(float), {
        "artifact": str(model_path),
        "artifact_sha256": _sha256(model_path),
        "epochs_observed": len(evaluation.get("valid", [])),
        "best_validation_score": max(evaluation.get("valid", [float("nan")])),
    }


def _tra_dataset(frames: PreparedFrames):
    from qlib.contrib.data.dataset import MTSDatasetH
    from qlib.data.dataset.handler import DataHandler
    from qlib.data.dataset.loader import StaticDataLoader

    feature = frames.raw.loc[:, FEATURE_NAMES]
    label = frames.raw.loc[:, ["LABEL0"]]
    handler = DataHandler(
        data_loader=StaticDataLoader({"feature": feature, "label": label})
    )
    timestamps = frames.raw.index.get_level_values("datetime")
    return MTSDatasetH(
        handler=handler,
        segments={
            "train": (timestamps.min(), TRAIN_END),
            "valid": (VALID_START, VALID_END),
            "test": (TEST_START, timestamps.max()),
        },
        seq_len=SEQUENCE_LENGTH,
        horizon=1,
        num_states=3,
        memory_mode="sample",
        batch_size=512,
        shuffle=True,
        drop_last=False,
    )


def _train_tra(
    frames: PreparedFrames, artifacts: Path
) -> tuple[pd.Series, dict[str, Any]]:
    from qlib.contrib.model.pytorch_tra import TRAModel

    logdir = artifacts / "qlib-tra"
    model = TRAModel(
        model_config={
            "input_size": len(FEATURE_NAMES),
            "hidden_size": 64,
            "num_layers": 2,
            "rnn_arch": "GRU",
            "use_attn": True,
            "dropout": 0.1,
        },
        tra_config={
            "num_states": 3,
            "hidden_size": 16,
            "num_layers": 1,
            "tau": 1.0,
            "src_info": "LR_TPE",
        },
        model_type="RNN",
        lr=0.001,
        n_epochs=12,
        early_stop=3,
        max_steps_per_epoch=100,
        lamb=1.0,
        rho=0.99,
        seed=SEED,
        logdir=str(logdir),
        eval_train=False,
        eval_test=False,
        pretrain=False,
        transport_method="router",
        memory_mode="sample",
    )
    evaluation: dict[str, Any] = {}
    dataset = _tra_dataset(frames)
    model.fit(dataset, evals_result=evaluation)
    dense = model.predict(dataset, segment="test")
    prediction = dense["score"].astype(float)
    model_path = logdir / "model.bin"
    return prediction, {
        "artifact": str(model_path),
        "artifact_sha256": _sha256(model_path),
        "epochs_observed": len(evaluation.get("valid", [])),
        "best_validation_ic": max(
            (row.get("IC", float("nan")) for row in evaluation.get("valid", [])),
            default=float("nan"),
        ),
    }


def ordered_predictions(
    prediction: pd.Series, test_keys: Iterable[SampleKey]
) -> list[float]:
    if not isinstance(prediction.index, pd.MultiIndex):
        raise ValueError("prediction index must identify datetime and instrument")
    values = []
    for key in test_keys:
        try:
            value = prediction.loc[(key.timestamp, key.instrument)]
        except KeyError as exc:
            raise ValueError(f"missing prediction for {key}") from exc
        if isinstance(value, pd.Series):
            if len(value) != 1:
                raise ValueError(f"duplicate prediction for {key}")
            value = value.iloc[0]
        values.append(float(value) / 100.0)
    return validate_prediction_values(values, expected=len(values))


def score_by_instrument(
    actual: list[float],
    predicted: list[float],
    keys: tuple[SampleKey, ...],
    *,
    round_trip_cost_bps: float,
) -> dict[str, Any]:
    if not (len(actual) == len(predicted) == len(keys)):
        raise ValueError("actual, prediction, and key samples must align")
    result = {
        "overall": summarize_predictions(
            actual,
            predicted,
            round_trip_cost_bps=round_trip_cost_bps,
        ),
        "by_symbol_timeframe": {},
    }
    for instrument in sorted({key.instrument for key in keys}):
        selected = [index for index, key in enumerate(keys) if key.instrument == instrument]
        symbol, timeframe = instrument.rsplit("_", 1)
        result["by_symbol_timeframe"][f"{symbol}:{timeframe}"] = summarize_predictions(
            [actual[index] for index in selected],
            [predicted[index] for index in selected],
            round_trip_cost_bps=round_trip_cost_bps,
        )
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not pd.notna(value):
        return None
    return value


def _write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(report), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_training(
    *,
    data_root: Path,
    chronos_report: Path,
    output: Path,
    artifacts: Path,
    models: Iterable[str],
) -> dict[str, Any]:
    selected = tuple(models)
    if not selected or any(name not in MODEL_NAMES for name in selected):
        raise ValueError(f"models must be selected from {MODEL_NAMES}")
    artifacts.mkdir(parents=True, exist_ok=True)
    runtime = _configure_torch()
    frames = prepare_frames(data_root)
    dataset_hash = _collection_sha256(data_root)
    base_path = output if output.exists() else chronos_report
    report = json.loads(base_path.read_text(encoding="utf-8"))
    if report.get("dataset_collection_sha256") != dataset_hash:
        raise ValueError("report and Qlib dataset hashes do not match")
    report["protocol"] = "chronos-qlib-locked-comparison-v1"
    report["qlib"] = {
        "seed": SEED,
        "runtime": runtime,
        "feature_names": list(FEATURE_NAMES),
        "sequence_length": SEQUENCE_LENGTH,
        "train_end": TRAIN_END.isoformat(),
        "valid_start": VALID_START.isoformat(),
        "valid_end": VALID_END.isoformat(),
        "test_origins": len(frames.test),
        "models": report.get("qlib", {}).get("models", {}),
    }
    actual_by_index = frames.test["LABEL0"].astype(float) / 100.0
    actual = [
        float(actual_by_index.loc[(key.timestamp, key.instrument)])
        for key in frames.test_keys
    ]
    round_trip_cost_bps = float(report["round_trip_cost_bps_per_active_sample"])
    for name in selected:
        if name == "tra":
            prediction, metadata = _train_tra(frames, artifacts)
        else:
            prediction, metadata = _train_standard(name, frames, artifacts)
        ordered = ordered_predictions(prediction, frames.test_keys)
        report["metrics"][f"qlib/{name}"] = score_by_instrument(
            actual,
            ordered,
            frames.test_keys,
            round_trip_cost_bps=round_trip_cost_bps,
        )
        report["qlib"]["models"][name] = metadata
        report["safety"] = {
            "mode": "RESEARCH_ONLY",
            "orders_created": False,
            "automatic_strategy_promotion": False,
            "paper_order_permission": False,
            "testnet_order_permission": False,
            "live_order_permission": False,
        }
        _write_report(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--chronos-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--models", nargs="+", choices=MODEL_NAMES, default=MODEL_NAMES)
    args = parser.parse_args()
    report = run_training(
        data_root=args.data_root,
        chronos_report=args.chronos_report,
        output=args.output,
        artifacts=args.artifacts,
        models=args.models,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "models": sorted(report["qlib"]["models"]),
                "safety": report["safety"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

