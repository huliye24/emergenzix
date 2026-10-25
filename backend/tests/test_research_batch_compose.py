"""Offline command boundary for the explicit one-job Compose operator script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "research_batch_once.sh"


def _fake_docker(tmp_path: Path) -> tuple[dict[str, str], Path]:
    executable = tmp_path / "docker"
    capture = tmp_path / "docker-calls.txt"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_ARGS_PATH"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    env["CAPTURE_ARGS_PATH"] = str(capture)
    return env, capture


@pytest.mark.parametrize("arguments", [
    [],
    ["a" * 63],
    ["A" * 64],
    ["a" * 65],
    ["a" * 64, "b" * 64],
    ["; echo hacked"],
    ["$(touch /tmp/not-an-id)"],
    ["../../secrets"],
    ["--help"],
])
def test_invalid_ids_never_call_docker(tmp_path: Path, arguments: list[str]) -> None:
    env, capture = _fake_docker(tmp_path)
    done = subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 2
    assert "Usage:" in done.stderr
    assert not capture.exists()


def test_explicit_valid_id_invokes_exact_one_job_on_shared_api_service(
    tmp_path: Path,
) -> None:
    env, capture = _fake_docker(tmp_path)
    batch_id = "0123456789abcdef" * 4
    done = subprocess.run(
        ["bash", str(SCRIPT), batch_id],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0
    args = capture.read_text(encoding="utf-8").splitlines()
    assert args == [
        "compose", "run", "--rm", "--no-deps", "-T", "api",
        "python", "-m", "emergenzix_api.research_batch_worker",
        "--batch-id", batch_id, "--db-path", "/data/emergenzix.db",
        "--execute-one",
    ]
    assert "--service-ports" not in args
    assert "--publish" not in args
    assert "--privileged" not in args


def test_compose_api_and_telegram_share_same_sqlite_volume() -> None:
    compose = (SCRIPT.parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert compose.count("EMERGENZIX_DB_PATH: /data/emergenzix.db") == 2
    assert compose.count("- emergenzix-data:/data") >= 2
    assert "EMERGENZIX_TELEGRAM_BATCH_ENABLED:" in compose
    assert "EMERGENZIX_VERSION:-0.1.0" in compose


def test_worker_requires_separate_explicit_execute_flag() -> None:
    worker = (SCRIPT.parents[1] / "backend" / "emergenzix_api"
              / "research_batch_worker.py").read_text(encoding="utf-8")
    assert '"--execute-one", action="store_true"' in worker
    assert 'if not args.execute_one:' in worker
    assert "run_one_batch_job(" in worker
