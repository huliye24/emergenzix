from emergenzix_api.brmg_g2_virtual_dataset import (
    PARTITION_SEEDS, audit_virtual_dataset, build_virtual_dataset, generate_path,
)
from emergenzix_api.brmg_scenarios import Partition


def test_virtual_dataset_is_reproducible_complete_and_fail_closed():
    first = build_virtual_dataset(bars_per_path=720)
    second = build_virtual_dataset(bars_per_path=720)
    assert first["dataset_sha256"] == second["dataset_sha256"]
    audit = audit_virtual_dataset(first)
    assert audit["decision"] == "READY_FOR_STAGE1_VIRTUAL_PRETRAINING"
    assert audit["partition_counts"] == {"TRAIN": 24, "DEVELOPMENT": 6, "SEALED_TEST": 6}
    assert audit["unique_phase_signatures"] >= 30
    assert .002 <= audit["return_sigma"] <= .05
    assert all(audit["checks"].values())


def test_partition_seed_cannot_cross_boundary():
    seed = PARTITION_SEEDS[Partition.TRAIN][0]
    try:
        generate_path(seed=seed, partition=Partition.DEVELOPMENT, bars=720)
    except ValueError as error:
        assert "frozen partition" in str(error)
    else:
        raise AssertionError("cross-partition seed was accepted")


def test_training_rows_have_market_and_teacher_labels_but_no_action_answer():
    path = generate_path(seed=PARTITION_SEEDS[Partition.TRAIN][0],
                         partition=Partition.TRAIN, bars=720)
    row = path["bars"][0]
    assert {"open_interest", "funding_rate", "bid_depth", "ask_depth",
            "spread_bps", "slippage_bps", "fee_bps", "labels"} <= row.keys()
    assert not ({"action", "best_action", "target_position", "side", "signal"} & row.keys())
    assert path["order_permission"] is False and path["position_permission"] is False
