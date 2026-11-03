from __future__ import annotations

import pytest

from emergenzix_api.chronos_walk_forward import (
    chronological_origins,
    local_model_snapshot,
    summarize_predictions,
)


def test_chronological_origins_keep_context_before_every_target() -> None:
    origins = chronological_origins(1_000, context_length=512, count=4, spacing=42)

    assert origins == sorted(origins)
    assert origins[-1] == 999
    assert all(origin >= 512 for origin in origins)
    assert all(current - previous == 42 for previous, current in zip(origins, origins[1:]))


def test_chronological_origins_reject_short_series() -> None:
    with pytest.raises(ValueError, match="too short"):
        chronological_origins(600, context_length=512, count=4, spacing=42)


def test_summary_applies_cost_only_to_active_long_samples() -> None:
    result = summarize_predictions(
        [0.02, -0.01, 0.03],
        [0.01, -0.02, 0.02],
        round_trip_cost_bps=30,
    )

    assert result["samples"] == 3
    assert result["direction_accuracy_pct"] == 100.0
    assert result["long_samples"] == 2
    assert result["sampled_gross_return_sum_pct"] == 5.0
    assert result["sampled_cost_adjusted_return_sum_pct"] == 4.4


def test_summary_omits_forecast_error_for_direction_only_baseline() -> None:
    result = summarize_predictions(
        [0.02, -0.01],
        [1.0, 1.0],
        round_trip_cost_bps=30,
        include_forecast_error=False,
    )

    assert result["mae_return_pct"] is None
    assert result["direction_accuracy_pct"] == 50.0


def test_summary_rejects_non_finite_metrics() -> None:
    with pytest.raises(ValueError, match="finite"):
        summarize_predictions([0.01], [float("nan")], round_trip_cost_bps=30)


def test_offline_model_resolution_pins_cache_revision(tmp_path) -> None:
    revision = "a" * 40
    repository = tmp_path / "hub" / "models--amazon--chronos-2"
    (repository / "refs").mkdir(parents=True)
    (repository / "refs" / "main").write_text(revision, encoding="utf-8")
    snapshot = repository / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"weights")

    assert local_model_snapshot("amazon/chronos-2", tmp_path) == str(snapshot)
    with pytest.raises(ValueError, match="allowlisted"):
        local_model_snapshot("example/unreviewed", tmp_path)
