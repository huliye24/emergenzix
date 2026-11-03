from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.model_signal_artifacts import (
    bars_sha256,
    build_signal_record,
    load_verified_model_signals,
    signal_record_sha256,
    verify_signal_record,
)
from emergenzix_api.schemas import Bar


def _bars() -> list[Bar]:
    start = datetime(2026, 11, 3, tzinfo=timezone.utc)
    return [
        Bar(timestamp=start + timedelta(hours=index), open=100 + index,
            high=102 + index, low=99 + index, close=101 + index, volume=1_000)
        for index in range(40)
    ]


def _record(bars: list[Bar]) -> dict[str, object]:
    end_at = bars[-1].timestamp
    record: dict[str, object] = {
        "schema_version": "emergenzix-model-signal/v1",
        "generated_at": (end_at + timedelta(minutes=5)).isoformat(),
        "model": {
            "family": "Chronos", "name": "Chronos-Bolt-Small", "version": "2.3.2",
            "source_revision": "0aba28e", "artifact_sha256": "a" * 64,
        },
        "input": {
            "symbol": "BTC/USDT", "timeframe": "1h", "bars_count": len(bars),
            "bars_sha256": bars_sha256(bars),
            "start_at": bars[0].timestamp.isoformat(), "end_at": end_at.isoformat(),
        },
        "prediction": {
            "target_at": (end_at + timedelta(hours=1)).isoformat(),
            "direction": "UP", "score": 0.61, "calibrated_probability": False,
        },
        "order_permission": False,
    }
    record["record_sha256"] = signal_record_sha256(record)
    return record


def test_verified_model_signal_requires_exact_snapshot_and_next_timestamp() -> None:
    bars = _bars()
    result = verify_signal_record(
        _record(bars), bars=bars, symbol="BTC/USDT", timeframe="1h",
        now=bars[-1].timestamp + timedelta(minutes=10),
    )
    assert result["status"] == "verified"
    assert result["direction"] == "UP"
    assert result["order_permission"] is False


def test_builder_emits_a_self_verifying_advisory_record() -> None:
    bars = _bars()
    generated_at = bars[-1].timestamp + timedelta(minutes=5)
    record = build_signal_record(
        family="Qlib", name="Transformer", version="0.9.8",
        source_revision="5435523", artifact_sha256="c" * 64,
        bars=bars, symbol="BTC/USDT", timeframe="1h",
        generated_at=generated_at, direction="DOWN", score=0.5,
        target_at=bars[-1].timestamp + timedelta(hours=1),
        prediction_details={"predicted_return": -0.001},
    )

    result = verify_signal_record(
        record, bars=bars, symbol="BTC/USDT", timeframe="1h",
        now=generated_at + timedelta(minutes=1),
    )
    assert result["family"] == "Qlib"
    assert result["direction"] == "DOWN"


@pytest.mark.parametrize("mutation", ["snapshot", "target", "permission"])
def test_model_signal_tampering_fails_closed(mutation: str) -> None:
    bars = _bars()
    record = _record(bars)
    if mutation == "snapshot":
        record["input"]["bars_sha256"] = "b" * 64
    elif mutation == "target":
        record["prediction"]["target_at"] = bars[-1].timestamp.isoformat()
    else:
        record["order_permission"] = True
    record["record_sha256"] = signal_record_sha256(record)
    with pytest.raises(ValueError):
        verify_signal_record(
            record, bars=bars, symbol="BTC/USDT", timeframe="1h",
            now=bars[-1].timestamp + timedelta(minutes=10),
        )


def test_malformed_model_object_is_rejected_without_crashing(tmp_path) -> None:
    bars = _bars()
    artifact = tmp_path / "signals.json"
    artifact.write_text('{"signals":[{"model":"invalid"}]}', encoding="utf-8")

    statuses = load_verified_model_signals(
        artifact, bars=bars, symbol="BTC/USDT", timeframe="1h",
        now=bars[-1].timestamp + timedelta(minutes=10),
    )

    assert [item["status"] for item in statuses] == [
        "artifact_unavailable", "artifact_unavailable"
    ]
    assert all(item["order_permission"] is False for item in statuses)
