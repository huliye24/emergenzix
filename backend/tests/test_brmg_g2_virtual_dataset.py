from emergenzix_api.brmg_g2_virtual_dataset import (
    CYCLE_BARS, PARTITION_SEEDS, POST_WARMUP_CYCLES, WARMUP_BARS,
    audit_virtual_dataset, build_virtual_dataset, generate_path,
    write_virtual_dataset_directory,
)
from emergenzix_api.brmg_scenarios import Partition
from emergenzix_api.brla_g2_training_protocol import OrderState


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


def test_streaming_directory_keeps_paths_separate(tmp_path):
    manifest = write_virtual_dataset_directory(tmp_path / "dataset", bars_per_path=720)
    assert len(manifest["paths"]) == 36
    assert len({row["filename"] for row in manifest["paths"]}) == 36
    assert all((tmp_path / "dataset" / row["filename"]).exists()
               for row in manifest["paths"])


def test_post_warmup_curriculum_contains_three_complete_cycles():
    bars = WARMUP_BARS + POST_WARMUP_CYCLES * CYCLE_BARS
    path = generate_path(seed=PARTITION_SEEDS[Partition.TRAIN][0],
                         partition=Partition.TRAIN, bars=bars)
    post = path["bars"][WARMUP_BARS:]
    for cycle in range(POST_WARMUP_CYCLES):
        states = {row["labels"]["order_state"]
                  for row in post[cycle * CYCLE_BARS:(cycle + 1) * CYCLE_BARS]}
        assert states == {state.value for state in OrderState}
    assert all("cycle_id" not in row and "seed" not in row for row in post)
