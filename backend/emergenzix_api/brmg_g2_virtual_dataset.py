"""Deterministic, PAPER-only BRMG-G2 paths for representation pretraining."""

from __future__ import annotations

import hashlib
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

import numpy as np

from .brmg_scenarios import Partition
from .brla_g2_training_protocol import OrderState

PROTOCOL = "emergenzix-brmg-g2-virtual-dataset-v1"
WARMUP_BARS = 180 * 96
POST_WARMUP_CYCLES = 3
CYCLE_BARS = 30 * 96
STATES = tuple(state.value for state in OrderState)
FORBIDDEN_ACTION_FIELDS = frozenset({"action", "best_action", "target_position", "side", "signal"})
PARTITION_SEEDS = {
    Partition.TRAIN: tuple(range(2026111100, 2026111124)),
    Partition.DEVELOPMENT: tuple(range(2026121100, 2026121106)),
    Partition.SEALED_TEST: tuple(range(2026131100, 2026131106)),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _clip(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def generate_path(*, seed: int, partition: Partition, bars: int = 1440) -> dict[str, Any]:
    """Generate one causal market path; state labels are not trading actions."""
    if seed not in PARTITION_SEEDS[partition] or bars < 720:
        raise ValueError("seed must belong to its frozen partition and bars must be >= 720")
    rng = np.random.default_rng(seed)
    price = float(rng.uniform(40_000, 100_000))
    oi = float(rng.uniform(8e8, 2e9))
    rows: list[dict[str, Any]] = []
    start = datetime(2045, 1, 1, tzinfo=timezone.utc) + timedelta(days=seed % 3000)
    cycle_bars = CYCLE_BARS if bars >= WARMUP_BARS + POST_WARMUP_CYCLES * CYCLE_BARS else bars
    cycle_count = math.ceil(bars / cycle_bars)
    phase_ends = [np.cumsum(rng.dirichlet(np.full(len(STATES), 8.0))) * cycle_bars
                  for _ in range(cycle_count)]
    drifts = (.00035, .00012, 0.0, -.00018, -.00042, -.00065)
    for index in range(bars):
        cycle_id, cycle_offset = divmod(index, cycle_bars)
        state_index = min(int(np.searchsorted(phase_ends[cycle_id], cycle_offset,
                                              side="right")),
                          len(STATES) - 1)
        cycle = cycle_offset / max(cycle_bars - 1, 1)
        old_strength = _clip(.88 - .72 * cycle + rng.normal(0, .025))
        new_strength = _clip(.08 + .82 * cycle + rng.normal(0, .025))
        maturity = _clip(cycle + rng.normal(0, .018))
        remaining = _clip(1 - .78 * maturity + rng.normal(0, .025))
        exhaustion = _clip(.10 + .72 * maturity + rng.normal(0, .035))
        entry_quality = _clip(math.exp(-((maturity - .34) / .23) ** 2)
                              * remaining * (1 - .45 * exhaustion))
        late_risk = _clip(.62 * max((maturity - .52) / .48, 0) + .38 * exhaustion)
        volatility = .004 + .009 * (state_index in {2, 3, 5}) + float(rng.uniform(0, .003))
        ret = drifts[state_index] + float(rng.normal(0, volatility))
        opening, closing = price, price * math.exp(ret)
        wick = abs(float(rng.normal(volatility * .75, volatility * .22))) + abs(ret) * .30
        volume = float(rng.lognormal(8.0 + 1.8 * abs(ret), .35))
        oi_change = (.0008 if state_index < 2 else -.0012 if state_index >= 3 else 0)
        oi = max(1e6, oi * math.exp(oi_change + rng.normal(0, .0035)))
        funding = float(np.clip(.00035 * (old_strength - new_strength)
                                + rng.normal(0, .00008), -.003, .003))
        bid_depth = float(rng.lognormal(11.2 - .55 * maturity, .28))
        ask_depth = float(rng.lognormal(11.2 + .20 * maturity, .28))
        spread = float(np.clip(1.2 + 18 * volatility + 5 * exhaustion
                               + rng.lognormal(-.2, .35), .2, 40))
        row = {
            "timestamp": (start + timedelta(minutes=15 * index)).isoformat(),
            "open": opening, "high": max(opening, closing) * math.exp(wick),
            "low": min(opening, closing) * math.exp(-wick), "close": closing,
            "base_volume": volume, "open_interest": oi, "funding_rate": funding,
            "bid_depth": bid_depth, "ask_depth": ask_depth,
            "spread_bps": spread, "slippage_bps": spread * .45,
            "fee_bps": 4.0,
            "labels": {"order_state": STATES[state_index],
                "old_order_strength": old_strength, "new_order_strength": new_strength,
                "trend_maturity": maturity, "remaining_potential": remaining,
                "entry_location_quality": entry_quality,
                "feedback_exhaustion": exhaustion, "late_entry_risk": late_risk},
        }
        rows.append(row); price = closing
    identity = {"protocol": PROTOCOL, "partition": partition.value,
                "seed": seed, "bars": rows}
    return {**identity, "path_sha256": _hash(identity), "synthetic": True,
            "mode": "RESEARCH", "automatic_promotion": False,
            "position_permission": False, "order_permission": False}


def build_virtual_dataset(*, bars_per_path: int = 1440) -> dict[str, Any]:
    paths = [generate_path(seed=seed, partition=partition, bars=bars_per_path)
             for partition, seeds in PARTITION_SEEDS.items() for seed in seeds]
    manifest = {"protocol": PROTOCOL, "paths": paths,
        "partition_seeds": {key.value: value for key, value in PARTITION_SEEDS.items()},
        "synthetic": True, "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    manifest["dataset_sha256"] = _hash(manifest)
    return manifest


def write_virtual_dataset_directory(output: Path, *, bars_per_path: int = 20000) -> dict[str, Any]:
    """Write paths independently so long-horizon curricula stay memory bounded."""
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for partition, seeds in PARTITION_SEEDS.items():
        for seed in seeds:
            path = generate_path(seed=seed, partition=partition, bars=bars_per_path)
            filename = f"{partition.value.lower()}-{seed}.json.gz"
            with gzip.open(output / filename, "wt", encoding="utf-8") as handle:
                json.dump(path, handle, separators=(",", ":"))
            records.append({"partition": partition.value, "seed": seed,
                "filename": filename, "path_sha256": path["path_sha256"],
                "bar_count": len(path["bars"])})
    base = {"protocol": PROTOCOL, "format": "PATH_JSON_GZIP_V1",
        "bars_per_path": bars_per_path, "paths": records,
        "curriculum": {"warmup_bars": WARMUP_BARS,
            "post_warmup_cycles": POST_WARMUP_CYCLES, "cycle_bars": CYCLE_BARS,
            "cycle_id_exposed_to_model": False},
        "synthetic": True, "mode": "RESEARCH", "automatic_promotion": False,
        "position_permission": False, "order_permission": False}
    base["dataset_sha256"] = _hash(base)
    (output / "manifest.json").write_text(json.dumps(base, indent=2), encoding="utf-8")
    return base


def audit_virtual_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    paths = dataset.get("paths", [])
    hashes = [path.get("path_sha256") for path in paths]
    states = {row["labels"]["order_state"] for path in paths for row in path["bars"]}
    maturities = [row["labels"]["trend_maturity"] for path in paths for row in path["bars"]]
    bands = [sum(low <= value < high if high < 1 else low <= value <= high
                 for value in maturities) for low, high in
             ((0, .2), (.2, .5), (.5, .7), (.7, .85), (.85, 1))]
    row_keys = {key for path in paths for row in path["bars"] for key in row}
    required = {"timestamp", "open", "high", "low", "close", "base_volume",
        "open_interest", "funding_rate", "bid_depth", "ask_depth", "spread_bps",
        "slippage_bps", "fee_bps", "labels"}
    partitions = {name: {path["path_sha256"] for path in paths
                         if path["partition"] == name} for name in
                  ("TRAIN", "DEVELOPMENT", "SEALED_TEST")}
    market_rows = [row for path in paths for row in path["bars"]]
    numeric_fields = required - {"timestamp", "labels"}
    finite_positive = all(math.isfinite(float(row[field])) and float(row[field]) > 0
                          for row in market_rows for field in numeric_fields
                          if field != "funding_rate")
    ohlc_valid = all(row["low"] <= min(row["open"], row["close"])
                     <= max(row["open"], row["close"]) <= row["high"]
                     for row in market_rows)
    returns = [math.log(row["close"] / row["open"]) for row in market_rows]
    return_sigma = float(np.std(returns))
    phase_signatures = set()
    for path in paths:
        signature, previous = [], None
        for index, row in enumerate(path["bars"]):
            state = row["labels"]["order_state"]
            if state != previous:
                signature.append((state, index)); previous = state
        phase_signatures.add(tuple(signature))
    checks = {
        "dataset_identity_valid": dataset.get("dataset_sha256") == _hash({
            key: value for key, value in dataset.items() if key != "dataset_sha256"}),
        "partition_hashes_disjoint": all(partitions[a].isdisjoint(partitions[b])
            for index, a in enumerate(partitions) for b in tuple(partitions)[index + 1:]),
        "frozen_seed_sets_match": all(tuple(path["seed"] for path in paths
            if path["partition"] == partition.value) == seeds
            for partition, seeds in PARTITION_SEEDS.items()),
        "all_six_states_present": states == set(STATES),
        "all_five_maturity_bands_present": all(count > 0 for count in bands),
        "required_market_fields_present": required <= row_keys,
        "no_action_answer_fields": not (row_keys & FORBIDDEN_ACTION_FIELDS),
        "paper_permissions_closed": all(path.get("order_permission") is False
            and path.get("position_permission") is False for path in paths),
        "unique_paths": len(hashes) == len(set(hashes)),
        "ohlc_and_numeric_values_valid": finite_positive and ohlc_valid,
        "return_volatility_plausible": .002 <= return_sigma <= .05,
        "funding_and_costs_bounded": all(abs(row["funding_rate"]) <= .003
            and .2 <= row["spread_bps"] <= 40 and row["fee_bps"] == 4.0
            for row in market_rows),
        "phase_boundaries_not_fixed": len(phase_signatures) >= len(paths) * .8,
    }
    ready = all(checks.values())
    return {"protocol": "emergenzix-brmg-g2-virtual-audit-v1",
        "dataset_sha256": dataset.get("dataset_sha256"),
        "path_count": len(paths), "bar_count": sum(len(path["bars"]) for path in paths),
        "partition_counts": {key: len(value) for key, value in partitions.items()},
        "state_coverage": sorted(states), "maturity_band_counts": bands,
        "return_sigma": return_sigma, "unique_phase_signatures": len(phase_signatures),
        "checks": checks,
        "decision": "READY_FOR_STAGE1_VIRTUAL_PRETRAINING" if ready else "NOT_READY",
        "automatic_promotion": False, "position_permission": False,
        "order_permission": False}
