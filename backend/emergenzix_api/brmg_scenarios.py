"""Canonical BRMG scenario assets and fail-closed research partitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

import numpy as np

PROTOCOL = "emergenzix-brmg-scenario-v1"


class SourceKind(StrEnum):
    CHRONOS = "CHRONOS"
    HISTORICAL_RESAMPLE = "HISTORICAL_RESAMPLE"
    EXTREME_STRESS = "EXTREME_STRESS"


class Partition(StrEnum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"
    SEALED_TEST = "SEALED_TEST"


@dataclass(frozen=True, slots=True)
class BRMGScenario:
    scenario_id: str
    source_kind: SourceKind
    generator_id: str
    source_fingerprint: str
    bars: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...]
    path_content_sha256: str
    scenario_sha256: str
    protocol: str = PROTOCOL
    synthetic: bool = True
    mode: str = "PAPER"
    automatic_promotion: bool = False
    order_permission: bool = False


@dataclass(frozen=True, slots=True)
class PartitionRecord:
    scenario_id: str
    scenario_sha256: str
    path_content_sha256: str
    partition: Partition


@dataclass(frozen=True, slots=True)
class PartitionManifest:
    manifest_id: str
    development_salt_sha256: str
    sealed_access_token_sha256: str
    records: tuple[PartitionRecord, ...]
    manifest_sha256: str
    protocol: str = "emergenzix-brmg-partitions-v1"
    mode: str = "PAPER"
    automatic_promotion: bool = False
    order_permission: bool = False


def _canonical_bars(bars: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if len(bars) < 15:
        raise ValueError("a BRMG scenario requires at least 15 bars")
    result = []
    previous_timestamp: datetime | None = None
    for ordinal, source in enumerate(bars):
        row = {"ordinal": ordinal, "timestamp": str(source["timestamp"]),
               "open": float(source["open"]), "high": float(source["high"]),
               "low": float(source["low"]), "close": float(source["close"]),
               "base_volume": float(source.get("base_volume", source.get("volume", 0)))}
        numeric = tuple(row[name] for name in ("open", "high", "low", "close", "base_volume"))
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if (timestamp.tzinfo is None or timestamp.utcoffset() is None
                or any(not math.isfinite(value) or value <= 0 for value in numeric)
                or row["low"] > min(row["open"], row["close"])
                or row["high"] < max(row["open"], row["close"])
                or (previous_timestamp is not None and timestamp <= previous_timestamp)):
            raise ValueError("bars must be positive, ordered and valid OHLCV")
        previous_timestamp = timestamp
        result.append(row)
    return tuple(result)


def _scenario(*, scenario_id: str, source_kind: SourceKind, generator_id: str,
              source_fingerprint: str, bars: Sequence[Mapping[str, Any]],
              limitations: Sequence[str]) -> BRMGScenario:
    if not scenario_id.strip() or not generator_id.strip() or len(source_fingerprint) != 64:
        raise ValueError("scenario identity, generator and 64-character source hash are required")
    canonical = _canonical_bars(bars)
    path_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    base = BRMGScenario(scenario_id, source_kind, generator_id, source_fingerprint,
                        canonical, tuple(limitations), path_hash, "")
    digest = hashlib.sha256(json.dumps(asdict(base), sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return replace(base, scenario_sha256=digest)


def adapt_historical_resample(path: Mapping[str, Any]) -> BRMGScenario:
    return _scenario(scenario_id=f"history-{path['path_sha256'][:16]}",
        source_kind=SourceKind.HISTORICAL_RESAMPLE,
        generator_id=str(path.get("protocol", "historical-resample")),
        source_fingerprint=str(path["path_sha256"]), bars=path["bars"],
        limitations=("Bars are recomposed from historical bear-regime observations.",))


def adapt_chronos_closes(closes: Sequence[float], *, scenario_id: str,
                         model_snapshot_sha256: str, historical_sigma: float,
                         median_volume: float = 1000.0) -> BRMGScenario:
    values = [float(value) for value in closes]
    if len(values) < 16 or historical_sigma <= 0 or median_volume <= 0:
        raise ValueError("Chronos closes and scale parameters are invalid")
    bars = []
    for index, (opening, closing) in enumerate(zip(values, values[1:])):
        movement = abs(math.log(closing / opening))
        wick = max(historical_sigma * .35, movement * .25)
        timestamp = datetime(2041, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * index)
        bars.append({"timestamp": timestamp.isoformat(),
            "open": opening, "high": max(opening, closing) * math.exp(wick),
            "low": min(opening, closing) * math.exp(-wick), "close": closing,
            "base_volume": median_volume * (1 + movement / historical_sigma)})
    return _scenario(scenario_id=scenario_id, source_kind=SourceKind.CHRONOS,
        generator_id="amazon/chronos-bolt-small", source_fingerprint=model_snapshot_sha256,
        bars=bars, limitations=("OHLCV is derived from a Chronos close forecast.",
            "Volume and wicks are deterministic proxies."))


def generate_extreme_stress(*, seed: int, scenario_id: str,
                            bars: int = 120, start_price: float = 100_000) -> BRMGScenario:
    if bars < 15 or start_price <= 0:
        raise ValueError("stress length and start price are invalid")
    rng = np.random.default_rng(seed)
    output, price = [], float(start_price)
    for index in range(bars):
        shock = (-.035 if index in {bars // 3, bars // 3 + 1} else
                 .028 if index == bars // 2 else -.0015)
        ret = shock + float(rng.normal(0, .009 if index >= bars // 3 else .004))
        opening, closing = price, price * math.exp(ret)
        wick = .012 + abs(ret) * .45
        timestamp = datetime(2042, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * index)
        output.append({"timestamp": timestamp.isoformat(),
            "open": opening, "high": max(opening, closing) * math.exp(wick),
            "low": min(opening, closing) * math.exp(-wick), "close": closing,
            "base_volume": 1000 * (1 + abs(ret) * 35)})
        price = closing
    source_hash = hashlib.sha256(f"stress-v1:{seed}:{bars}:{start_price}".encode()).hexdigest()
    return _scenario(scenario_id=scenario_id, source_kind=SourceKind.EXTREME_STRESS,
        generator_id="brmg-extreme-stress-v1", source_fingerprint=source_hash, bars=output,
        limitations=("This is a deterministic synthetic tail stress, not a probability forecast.",))


def create_partition_manifest(scenarios: Sequence[BRMGScenario], *,
                              development_salt: str,
                              sealed_access_token: str) -> PartitionManifest:
    if len(scenarios) < 3 or len(development_salt) < 16 or len(sealed_access_token) < 32:
        raise ValueError("at least three scenarios and strong partition inputs are required")
    scenario_hashes = [row.scenario_sha256 for row in scenarios]
    content_hashes = [row.path_content_sha256 for row in scenarios]
    if len(set(scenario_hashes)) != len(scenarios) or len(set(content_hashes)) != len(scenarios):
        raise ValueError("duplicate scenarios or duplicate path content cannot cross partitions")
    ranked = sorted(scenarios, key=lambda row: hashlib.sha256(
        f"{development_salt}:{row.scenario_sha256}".encode()).hexdigest())
    total = len(ranked)
    train_count = max(1, int(total * .70))
    development_count = max(1, int(total * .15))
    if train_count + development_count >= total:
        train_count = total - 2; development_count = 1
    records = []
    for index, row in enumerate(ranked):
        partition = (Partition.TRAIN if index < train_count else
                     Partition.DEVELOPMENT if index < train_count + development_count
                     else Partition.SEALED_TEST)
        records.append(PartitionRecord(row.scenario_id, row.scenario_sha256,
                                       row.path_content_sha256, partition))
    salt_hash = hashlib.sha256(development_salt.encode()).hexdigest()
    token_hash = hashlib.sha256(sealed_access_token.encode()).hexdigest()
    manifest_id = f"brmg-split-{hashlib.sha256(''.join(scenario_hashes).encode()).hexdigest()[:16]}"
    base = PartitionManifest(manifest_id, salt_hash, token_hash, tuple(records), "")
    digest = hashlib.sha256(json.dumps(asdict(base), sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return replace(base, manifest_sha256=digest)


def select_partition(manifest: PartitionManifest, scenarios: Sequence[BRMGScenario], *,
                     purpose: Partition, sealed_access_token: str | None = None
                     ) -> tuple[BRMGScenario, ...]:
    expected = {record.scenario_sha256: record for record in manifest.records}
    supplied = {scenario.scenario_sha256: scenario for scenario in scenarios}
    if set(expected) != set(supplied):
        raise ValueError("scenario set does not match the frozen partition manifest")
    if purpose is Partition.SEALED_TEST:
        if sealed_access_token is None or not hmac.compare_digest(
                hashlib.sha256(sealed_access_token.encode()).hexdigest(),
                manifest.sealed_access_token_sha256):
            raise PermissionError("sealed test requires its external access token")
    return tuple(supplied[record.scenario_sha256] for record in manifest.records
                 if record.partition is purpose)


def scenario_as_training_path(scenario: BRMGScenario) -> dict[str, Any]:
    return {"protocol": scenario.protocol, "synthetic": True,
            "bars": [dict(row) for row in scenario.bars],
            "path_sha256": scenario.path_content_sha256,
            "automatic_promotion": False, "order_permission": False}


def scenario_from_dict(payload: Mapping[str, Any]) -> BRMGScenario:
    rebuilt = _scenario(scenario_id=str(payload["scenario_id"]),
        source_kind=SourceKind(payload["source_kind"]),
        generator_id=str(payload["generator_id"]),
        source_fingerprint=str(payload["source_fingerprint"]),
        bars=payload["bars"], limitations=payload["limitations"])
    if (payload.get("protocol") != PROTOCOL or payload.get("mode") != "PAPER"
            or payload.get("automatic_promotion") is not False
            or payload.get("order_permission") is not False
            or payload.get("path_content_sha256") != rebuilt.path_content_sha256
            or payload.get("scenario_sha256") != rebuilt.scenario_sha256):
        raise ValueError("scenario payload failed identity or safety verification")
    return rebuilt


def scenario_bundle(scenarios: Sequence[BRMGScenario]) -> dict[str, Any]:
    rows = [asdict(row) for row in scenarios]
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": "emergenzix-brmg-scenario-bundle-v1",
            "scenarios": rows, "bundle_sha256": digest,
            "mode": "PAPER", "automatic_promotion": False, "order_permission": False}


def scenarios_from_bundle(payload: Mapping[str, Any]) -> tuple[BRMGScenario, ...]:
    if (payload.get("schema_version") != "emergenzix-brmg-scenario-bundle-v1"
            or payload.get("mode") != "PAPER"
            or payload.get("automatic_promotion") is not False
            or payload.get("order_permission") is not False):
        raise ValueError("invalid or unsafe BRMG scenario bundle")
    rows = payload.get("scenarios")
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    if digest != payload.get("bundle_sha256"):
        raise ValueError("BRMG scenario bundle hash mismatch")
    return tuple(scenario_from_dict(row) for row in rows)


def partition_manifest_dict(manifest: PartitionManifest) -> dict[str, Any]:
    return asdict(manifest)


def partition_manifest_from_dict(payload: Mapping[str, Any]) -> PartitionManifest:
    records = tuple(PartitionRecord(scenario_id=str(row["scenario_id"]),
        scenario_sha256=str(row["scenario_sha256"]),
        path_content_sha256=str(row["path_content_sha256"]),
        partition=Partition(row["partition"])) for row in payload["records"])
    base = PartitionManifest(manifest_id=str(payload["manifest_id"]),
        development_salt_sha256=str(payload["development_salt_sha256"]),
        sealed_access_token_sha256=str(payload["sealed_access_token_sha256"]),
        records=records, manifest_sha256="")
    digest = hashlib.sha256(json.dumps(asdict(base), sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    if (payload.get("protocol") != base.protocol or payload.get("mode") != "PAPER"
            or payload.get("automatic_promotion") is not False
            or payload.get("order_permission") is not False
            or payload.get("manifest_sha256") != digest):
        raise ValueError("partition manifest failed identity or safety verification")
    return replace(base, manifest_sha256=digest)
