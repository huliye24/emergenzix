import pytest

from emergenzix_api.brla_g2_encoder_audit import audit_encoder


def test_missing_checkpoint_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit_encoder(tmp_path / "dataset", tmp_path / "missing.pt")


def test_audit_protocol_has_no_trading_authority():
    from emergenzix_api import brla_g2_encoder_audit as module
    assert module.PROTOCOL == "emergenzix-brla-g2-order-encoder-audit-v1"
