import pytest

from emergenzix_api.brla_g2_order_encoder import (
    balanced_class_weights, build_examples, ordinal_cdf_loss, semantic_state_sha256,
)
from emergenzix_api.brmg_g2_virtual_dataset import build_virtual_dataset
from emergenzix_api.brla_g2_training_protocol import DataPurpose
from emergenzix_api.brmg_scenarios import Partition


def test_short_paths_fail_closed_instead_of_weakening_multitimeframe_protocol():
    dataset = build_virtual_dataset(bars_per_path=720)
    with pytest.raises(ValueError, match="180-day"):
        build_examples(dataset, partition=Partition.TRAIN,
                       purpose=DataPurpose.STAGE1_TRAIN)


def test_sealed_partition_cannot_be_opened_by_encoder_trainer():
    dataset = build_virtual_dataset(bars_per_path=720)
    with pytest.raises(PermissionError, match="frozen model and explicit access"):
        build_examples(dataset, partition=Partition.SEALED_TEST,
                       purpose=DataPurpose.FINAL_SEALED_EVAL)


def test_partition_purpose_mismatch_fails_closed():
    dataset = build_virtual_dataset(bars_per_path=720)
    with pytest.raises(PermissionError, match="not authorized"):
        build_examples(dataset, partition=Partition.DEVELOPMENT,
                       purpose=DataPurpose.STAGE1_TRAIN)


def test_semantic_model_hash_ignores_mapping_insertion_order():
    import numpy as np
    import torch
    left = {"b": torch.tensor([2.0]), "a": torch.tensor([1.0])}
    right = {"a": torch.tensor([1.0]), "b": torch.tensor([2.0])}
    metadata = {"protocol": "x", "mean": np.asarray([1.0], dtype=np.float32)}
    assert semantic_state_sha256(left, metadata) == semantic_state_sha256(right, metadata)


def test_balanced_weights_upweight_rare_states():
    import numpy as np
    weights = balanced_class_weights(np.asarray([0] * 10 + [1] * 5 + [2, 3, 4, 5]))
    assert weights[0] < weights[1] < weights[2]


def test_ordinal_loss_penalizes_distant_error_more_than_adjacent_error():
    import torch
    target = torch.tensor([2])
    adjacent = torch.full((1, 6), -8.0); adjacent[0, 3] = 8.0
    distant = torch.full((1, 6), -8.0); distant[0, 5] = 8.0
    assert ordinal_cdf_loss(torch, distant, target) > ordinal_cdf_loss(torch, adjacent, target)
