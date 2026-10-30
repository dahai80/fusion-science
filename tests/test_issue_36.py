from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SH = REPO_ROOT / "start.sh"
PID_FILE = REPO_ROOT / ".fusion-science.pid"

# The registry self-check greps `^  <service>:` then `port: <PORT>$` within the
# next 3 lines, so the temp entries must match that exact 2-space / 4-space
# shape. PORT is pinned to 11462 (the registry value) via FUSION_SCIENCE_PORT.
PORT = "11462"
SERVICE = "fusion-science"

REGISTRY_OK = f"services:\n  {SERVICE}:\n    port: {PORT}\n    purpose: test entry\n    boot: T2\n"
REGISTRY_WRONG_PORT = f"services:\n  {SERVICE}:\n    port: 9999\n    purpose: test entry\n    boot: T2\n"
REGISTRY_NO_ENTRY = "services:\n  some-other-svc:\n    port: 11462\n"


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _run_start(env: dict[str, str]) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env["FUSION_SCIENCE_PORT"] = PORT
    full_env.update(env)
    return subprocess.run(
        ["bash", str(START_SH), "start"],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def live_pid_file():
    # Seed the pid file with a live PID (the test process) so start() exits 0
    # at is_running instead of launching uvicorn. Back up + restore any real
    # pid file so a running daemon is not disturbed.
    backup = PID_FILE.read_text() if PID_FILE.exists() else None
    PID_FILE.write_text(str(os.getpid()))
    yield
    if backup is None:
        PID_FILE.unlink(missing_ok=True)
    else:
        PID_FILE.write_text(backup)


class TestRegistrySelfCheck:
    def test_refuse_when_no_entry(self, tmp_path):
        # Acceptance: refuse when the service is missing from a temp registry copy.
        reg = _write(tmp_path, "noreg.yaml", REGISTRY_NO_ENTRY)
        result = _run_start({"FUSION_REGISTRY": reg})
        assert result.returncode == 1
        assert "refusing to start" in result.stdout + result.stderr

    def test_refuse_when_port_mismatch(self, tmp_path):
        # Entry present but a different port than we bind = unregistered for us.
        reg = _write(tmp_path, "wrongport.yaml", REGISTRY_WRONG_PORT)
        result = _run_start({"FUSION_REGISTRY": reg})
        assert result.returncode == 1
        assert "refusing to start" in result.stdout + result.stderr

    def test_pass_when_entry_present(self, tmp_path, live_pid_file):
        # Acceptance: passes with the entry present. Reaching is_running (exit 0)
        # with no refusal proves the registry check passed.
        reg = _write(tmp_path, "ok.yaml", REGISTRY_OK)
        result = _run_start({"FUSION_REGISTRY": reg})
        assert result.returncode == 0
        assert "refusing to start" not in result.stdout + result.stderr

    def test_bypass_with_skip_flag(self, tmp_path, live_pid_file):
        # FUSION_SKIP_REGISTRY_CHECK=1 bypasses the refusal and continues.
        reg = _write(tmp_path, "noreg.yaml", REGISTRY_NO_ENTRY)
        result = _run_start({"FUSION_REGISTRY": reg, "FUSION_SKIP_REGISTRY_CHECK": "1"})
        assert result.returncode == 0
        assert "registry check bypassed" in result.stdout + result.stderr

    def test_missing_registry_file_warns_and_continues(self, tmp_path, live_pid_file):
        # Missing registry file = warn + continue (standalone deploy).
        missing = str(tmp_path / "does-not-exist.yaml")
        result = _run_start({"FUSION_REGISTRY": missing})
        assert result.returncode == 0
        assert "registry self-check skipped" in result.stdout + result.stderr
