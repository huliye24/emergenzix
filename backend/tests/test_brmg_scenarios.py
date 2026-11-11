from dataclasses import replace

import pytest

from emergenzix_api.brmg_scenarios import (
    Partition, SourceKind, adapt_chronos_closes, adapt_historical_resample,
    create_partition_manifest, generate_extreme_stress, scenario_as_training_path,
    partition_manifest_dict, partition_manifest_from_dict, scenario_bundle,
    scenarios_from_bundle, select_partition,
)


def scenarios():
    rows = [generate_extreme_stress(seed=index, scenario_id=f"stress-{index}")
            for index in range(6)]
    closes = [100 * (.998 ** index) for index in range(121)]
    rows.append(adapt_chronos_closes(closes, scenario_id="chronos-1",
        model_snapshot_sha256="a" * 64, historical_sigma=.01))
    historical = scenario_as_training_path(
        generate_extreme_stress(seed=99, scenario_id="history-source"))
    historical["protocol"] = "history-v1"
    historical["path_sha256"] = "b" * 64
    rows.append(adapt_historical_resample(historical))
    return rows


def test_three_source_types_share_one_canonical_schema():
    rows = scenarios()
    assert {row.source_kind for row in rows} == {
        SourceKind.CHRONOS, SourceKind.HISTORICAL_RESAMPLE, SourceKind.EXTREME_STRESS}
    assert all(row.protocol == "emergenzix-brmg-scenario-v1" for row in rows)
    assert all(len(row.path_content_sha256) == 64 and row.order_permission is False
               for row in rows)


def test_train_development_and_sealed_test_are_disjoint_and_complete():
    rows = scenarios()
    manifest = create_partition_manifest(rows, development_salt="development-salt-1",
        sealed_access_token="sealed-token-that-is-never-committed-1")
    train = select_partition(manifest, rows, purpose=Partition.TRAIN)
    development = select_partition(manifest, rows, purpose=Partition.DEVELOPMENT)
    sealed = select_partition(manifest, rows, purpose=Partition.SEALED_TEST,
        sealed_access_token="sealed-token-that-is-never-committed-1")
    hashes = [{row.path_content_sha256 for row in part}
              for part in (train, development, sealed)]
    assert all(hashes[index].isdisjoint(hashes[other])
               for index in range(3) for other in range(index + 1, 3))
    assert set.union(*hashes) == {row.path_content_sha256 for row in rows}


def test_sealed_test_cannot_be_read_as_training_or_without_external_token():
    rows = scenarios()
    manifest = create_partition_manifest(rows, development_salt="development-salt-1",
        sealed_access_token="sealed-token-that-is-never-committed-1")
    with pytest.raises(PermissionError):
        select_partition(manifest, rows, purpose=Partition.SEALED_TEST)
    train = select_partition(manifest, rows, purpose=Partition.TRAIN)
    sealed_hashes = {record.path_content_sha256 for record in manifest.records
                     if record.partition is Partition.SEALED_TEST}
    assert not ({row.path_content_sha256 for row in train} & sealed_hashes)


def test_duplicate_content_and_manifest_tampering_fail_closed():
    rows = scenarios()
    with pytest.raises(ValueError, match="duplicate"):
        create_partition_manifest([*rows, replace(rows[0], scenario_id="copy")],
            development_salt="development-salt-1",
            sealed_access_token="sealed-token-that-is-never-committed-1")
    manifest = create_partition_manifest(rows, development_salt="development-salt-1",
        sealed_access_token="sealed-token-that-is-never-committed-1")
    with pytest.raises(ValueError, match="does not match"):
        select_partition(manifest, rows[:-1], purpose=Partition.TRAIN)


def test_bundle_and_manifest_round_trip_verify_content_hashes():
    rows = scenarios()
    bundle = scenario_bundle(rows)
    manifest = create_partition_manifest(rows, development_salt="development-salt-1",
        sealed_access_token="sealed-token-that-is-never-committed-1")
    assert scenarios_from_bundle(bundle) == tuple(rows)
    assert partition_manifest_from_dict(partition_manifest_dict(manifest)) == manifest
    bundle["scenarios"][0]["bars"][0]["close"] *= 2
    with pytest.raises(ValueError, match="hash mismatch"):
        scenarios_from_bundle(bundle)
