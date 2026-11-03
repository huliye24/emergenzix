"""Export one exact-snapshot Chronos forecast as a fail-closed signal record.

This GPU-only command reads public OHLCV JSON, performs inference, and writes
an advisory artifact. It has no exchange credentials or order path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .chronos_walk_forward import ForecastJob, _model_predictions, local_model_snapshot
from .model_signal_artifacts import TIMEFRAME_SECONDS, build_signal_record
from .schemas import Bar


CHRONOS_VERSION = "2.3.2"
CHRONOS_SOURCE_REVISION = "0aba28e9360b6355d5a919596c90b16c25939f02"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_bars(payload: Mapping[str, Any], *, exclude_final_bar: bool) -> list[Bar]:
    raw = payload.get("bars")
    if not isinstance(raw, list):
        raise ValueError("input must contain a bars list")
    bars = [Bar.model_validate(item) for item in raw]
    if exclude_final_bar:
        bars = bars[:-1]
    if len(bars) < 50:
        raise ValueError("Chronos signal export requires at least 50 completed bars")
    if any(current.timestamp <= previous.timestamp for previous, current in zip(bars, bars[1:])):
        raise ValueError("bars must be strictly ordered and unique")
    return bars


def create_chronos_record(
    payload: Mapping[str, Any], *, model_id: str, cache: Path,
    generated_at: datetime, exclude_final_bar: bool = False,
) -> dict[str, Any]:
    """Run one real Chronos inference over the exact recorded close window."""
    bars = _completed_bars(payload, exclude_final_bar=exclude_final_bar)
    symbol = str(payload.get("symbol", ""))
    timeframe = str(payload.get("timeframe", ""))
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("unsupported timeframe")
    snapshot = Path(local_model_snapshot(model_id, cache))
    model_artifact = snapshot / "model.safetensors"
    context = tuple(float(bar.close) for bar in bars)
    forecast = _model_predictions(
        model_id,
        [ForecastJob(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=(bars[-1].timestamp + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])).isoformat(),
            context=context,
            last_close=context[-1],
            actual_close=context[-1],
        )],
        cache,
    )[0]
    predicted_return = forecast / context[-1] - 1.0
    direction = "UP" if predicted_return > 0 else "DOWN" if predicted_return < 0 else "NEUTRAL"
    return build_signal_record(
        family="Chronos",
        name=model_id,
        version=CHRONOS_VERSION,
        source_revision=CHRONOS_SOURCE_REVISION,
        artifact_sha256=_sha256(model_artifact),
        bars=bars,
        symbol=symbol,
        timeframe=timeframe,
        generated_at=generated_at,
        direction=direction,
        score=0.5,
        target_at=bars[-1].timestamp + timedelta(seconds=TIMEFRAME_SECONDS[timeframe]),
        prediction_details={
            "forecast_close": forecast,
            "predicted_return": predicted_return,
            "score_method": "neutral_unscaled_point_forecast",
            "model_revision": snapshot.name,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument(
        "--model-id", choices=("amazon/chronos-bolt-small", "amazon/chronos-2"),
        default="amazon/chronos-bolt-small",
    )
    parser.add_argument("--exclude-final-bar", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    record = create_chronos_record(
        payload,
        model_id=args.model_id,
        cache=args.cache,
        generated_at=datetime.now(timezone.utc),
        exclude_final_bar=args.exclude_final_bar,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"signals": [record]}, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps({
        "output": str(args.output),
        "record_sha256": record["record_sha256"],
        "direction": record["prediction"]["direction"],
        "order_permission": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
