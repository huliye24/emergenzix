import pytest

from emergenzix_api.brla_g2_order_encoder import build_examples, semantic_state_sha256
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
