from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "ops" / "systemd"


def test_cpu_schedule_is_classic_only_and_research_scoped() -> None:
    service = (SYSTEMD / "emergenzix-research-cpu.service").read_text()
    timer = (SYSTEMD / "emergenzix-research-cpu.timer").read_text()

    assert "--node-class cpu --job classic-turtle-matrix" in service
    assert "chronos-zero-shot" not in service
    assert "qlib-locked-models" not in service
    assert "--execute" in service
    assert "OnCalendar=*-*-* 02:15:00 UTC" in timer


def test_gpu_schedule_preserves_chronos_before_qlib() -> None:
    service = (SYSTEMD / "emergenzix-research-gpu.service").read_text()
    timer = (SYSTEMD / "emergenzix-research-gpu.timer").read_text()

    chronos = "--node-class gpu --job chronos-zero-shot"
    qlib = "--node-class gpu --job qlib-locked-models"
    assert service.index(chronos) < service.index(qlib)
    assert "HF_HUB_OFFLINE=1" in service
    assert "TRANSFORMERS_OFFLINE=1" in service
    assert "EnvironmentFile=/etc/emergenzix/research-registry.env" in service
    assert "--registry-db ${EMERGENZIX_REGISTRY_DB}" in service
    assert "--input-asset-id ${EMERGENZIX_INPUT_ASSET_ID}" in service
    assert "OnCalendar=Sun *-*-* 03:15:00 UTC" in timer


def test_schedules_expose_no_trading_or_secret_configuration() -> None:
    payload = "\n".join(
        path.read_text()
        for path in SYSTEMD.glob("emergenzix-research-*.*")
    ).lower()

    for forbidden in (
        "api_key",
        "api_secret",
        "binance",
        "live_order",
        "place_order",
        "withdraw",
    ):
        assert forbidden not in payload
