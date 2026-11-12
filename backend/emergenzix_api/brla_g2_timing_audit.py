"""Readiness audit for BRLA-G2 v2 trend timing labels."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from typing import Any, Mapping, Sequence

from .brla_g2_trend_timing import TimingStage1Dataset

PROTOCOL = "emergenzix-brla-g2-trend-timing-audit-v1"
MIN_BAND_EXAMPLES = 3
MAX_BOUNDARY_SATURATION = .35
MIN_HIGH_QUALITY_ENTRY_FRACTION = .10
MAX_MATURE_ENTRY_BLOCK_FRACTION = .50
MIN_ELIGIBLE_ENTRY_FRACTION = .10

MATURITY_BANDS = (
    ("FORMATION", 0.0, .20),
    ("FRONT_MIDDLE", .20, .50),
    ("EXPANSION", .50, .70),
    ("MATURE", .70, .85),
    ("EXHAUSTION", .85, 1.0000000001),
)
TIMING_FIELDS = ("trend_maturity", "remaining_potential", "entry_location_quality",
                 "feedback_exhaustion", "late_entry_risk")


def audit_timing_datasets(datasets: Sequence[TimingStage1Dataset], *,
                          base_audit: Mapping[str, Any],
                          source_sha256: str) -> dict[str, Any]:
    examples = [example for dataset in datasets for example in dataset.examples]
    if not examples or len(source_sha256) != 64:
        raise ValueError("timing datasets and source fingerprint are required")
    values = {field: [getattr(example.timing_label, field) for example in examples]
              for field in TIMING_FIELDS}
    summaries = {field: {"minimum": min(rows), "mean": statistics.fmean(rows),
                         "maximum": max(rows)} for field, rows in values.items()}
    saturation = {field: {
        "at_or_below_0_02": sum(value <= .02 for value in rows) / len(rows),
        "at_or_above_0_98": sum(value >= .98 for value in rows) / len(rows),
    } for field, rows in values.items()}
    maturity_counts = Counter()
    for value in values["trend_maturity"]:
        for name, lower, upper in MATURITY_BANDS:
            if lower <= value < upper:
                maturity_counts[name] += 1; break
    maturity = {name: {"count": maturity_counts[name],
                       "fraction": maturity_counts[name] / len(examples)}
                for name, _, _ in MATURITY_BANDS}
    high_quality = sum(value >= .50 for value in values["entry_location_quality"])
    eligible = sum(example.timing_label.trend_maturity < .70
                   and example.timing_label.entry_location_quality >= .20
                   for example in examples)
    mature_blocked = sum(value >= .70 for value in values["trend_maturity"])
    high_quality_fraction = high_quality / len(examples)
    eligible_fraction = eligible / len(examples)
    mature_blocked_fraction = mature_blocked / len(examples)
    checks = {
        "base_dataset_ready": base_audit.get("decision") == "READY_FOR_ENCODER_TRAINING",
        "all_maturity_bands_present": all(maturity[name]["count"] > 0
                                           for name, _, _ in MATURITY_BANDS),
        "minimum_examples_per_maturity_band": all(
            maturity[name]["count"] >= MIN_BAND_EXAMPLES for name, _, _ in MATURITY_BANDS),
        "timing_saturation_within_limit": all(
            max(rows.values()) <= MAX_BOUNDARY_SATURATION for rows in saturation.values()),
        "high_quality_entries_sufficient": high_quality >= MIN_BAND_EXAMPLES
            and high_quality_fraction >= MIN_HIGH_QUALITY_ENTRY_FRACTION,
        "mature_entry_block_not_dominant": mature_blocked_fraction
            <= MAX_MATURE_ENTRY_BLOCK_FRACTION,
        "eligible_entry_coverage_sufficient": eligible >= MIN_BAND_EXAMPLES
            and eligible_fraction >= MIN_ELIGIBLE_ENTRY_FRACTION,
    }
    report = {"protocol": PROTOCOL, "source_sha256": source_sha256,
        "base_audit_report_sha256": base_audit.get("report_sha256"),
        "example_count": len(examples), "timing_summaries": summaries,
        "timing_saturation": saturation, "maturity_bands": maturity,
        "high_quality_entry_count": high_quality,
        "high_quality_entry_fraction": high_quality_fraction,
        "eligible_entry_count": eligible, "eligible_entry_fraction": eligible_fraction,
        "mature_entry_block_count": mature_blocked,
        "mature_entry_block_fraction": mature_blocked_fraction,
        "checks": checks,
        "decision": "READY_FOR_TIMING_ENCODER_TRAINING" if all(checks.values())
                    else "NOT_READY_FOR_TIMING_ENCODER_TRAINING",
        "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return report
