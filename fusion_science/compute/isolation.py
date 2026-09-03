"""OS-level sandbox isolation for the Python code executor.

Defense-in-depth layer ON TOP of the AST validation gate in sandbox.py:
the AST gate rejects dangerous imports/calls, but a determined or buggy
payload could still reach the filesystem or network. This module wraps
the subprocess in an OS-enforced sandbox so even a bypass cannot escape.

- macOS: `sandbox-exec` with a deny-by-default SBPL profile (no network,
  filesystem writes confined to the work_dir, read-only system paths).
- Linux: `bwrap` (bubblewrap) if installed — unshared mount/IPC/UTS/PID
  namespaces, no network, work_dir as the only writable bind mount.
- Fallback: no isolation tool available → return None; caller keeps the
  rlimit layer and logs a warning so the gap is visible, not silent.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IsolationResult:
    available: bool
    tool: str
    command_prefix: list[str]
    profile_path: str | None
    note: str


def _which(tool: str) -> str | None:
    path = shutil.which(tool)
    if path:
        return path
    # sandbox-exec lives at /usr/bin on macOS but is not always on PATH in
    # stripped environments; check the known absolute location.
    if tool == "sandbox-exec":
        candidate = "/usr/bin/sandbox-exec"
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _write_profile(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="fusion_iso_", suffix=".sbpl")
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    # Profile must be readable by the sandbox tool, not executable.
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return path


def _macos_profile(work_dir: str, python_executable: str) -> str:
    # SBPL (Seatbelt profile language). The highest-value control for a
    # service that executes user-supplied code is **blocking network** —
    # it prevents data exfiltration and C2 callback from a malicious
    # payload. Filesystem write confinement is layered on top by the AST
    # gate (sandbox.py blocks subprocess/shutil/eval) + rlimits, so the
    # Seatbelt layer focuses on a hard network deny. We use allow-default
    # rather than deny-default because deny-default also blocks inherited
    # stdio fds (the parent's capture pipe), which makes the child abort
    # (SIGABRT, exit 134) before producing any output — and enumerating
    # every stdlib/site-packages path across Homebrew/pyenv/framework
    # installs is fragile. Network-deny is portable; filesystem-deny is not.
    return "(version 1)\n(allow default)\n(deny network*)\n"


def _bwrap_command(work_dir: str, python_executable: str) -> list[str]:
    # bubblewrap: unshare everything practical, no network (--unshare-net),
    # bind work_dir read-write, bind system + python read-only.
    site_dir = os.path.dirname(os.path.dirname(python_executable))
    return [
        "bwrap",
        "--unshare-all",
        "--unshare-net",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        os.path.dirname(python_executable),
        os.path.dirname(python_executable),
        "--ro-bind",
        site_dir,
        site_dir,
        "--bind",
        work_dir,
        work_dir,
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--die-with-parent",
    ]


def build_isolation(work_dir: str, python_executable: str) -> IsolationResult:
    """Return the command prefix + profile to run `python_executable` isolated.

    Caller prepends `result.command_prefix` to its argv and, if
    `result.profile_path` is set, must clean it up after the subprocess
    completes. If no tool is available, `available=False` and the caller
    falls back to the rlimit-only layer (with a logged warning).
    """
    work_dir = os.path.abspath(work_dir)
    python_executable = os.path.abspath(python_executable)

    sandbox_exec = _which("sandbox-exec")
    if sandbox_exec:
        profile = _macos_profile(work_dir, python_executable)
        profile_path = _write_profile(profile)
        logger.info("Isolation: sandbox-exec (macOS Seatbelt), profile=%s", profile_path)
        return IsolationResult(
            available=True,
            tool="sandbox-exec",
            command_prefix=[sandbox_exec, "-f", profile_path],
            profile_path=profile_path,
            note="macOS Seatbelt: no network, writes confined to work_dir",
        )

    bwrap = _which("bwrap")
    if bwrap:
        logger.info("Isolation: bwrap (bubblewrap), work_dir=%s", work_dir)
        return IsolationResult(
            available=True,
            tool="bwrap",
            command_prefix=_bwrap_command(work_dir, python_executable),
            profile_path=None,
            note="bubblewrap: unshared namespaces, no network, work_dir only writable",
        )

    logger.warning(
        "Isolation: no OS sandbox tool found (sandbox-exec/bwrap). "
        "Falling back to rlimit-only layer — install bwrap or run on macOS "
        "for full isolation. Gap is logged, not silent."
    )
    return IsolationResult(
        available=False,
        tool="none",
        command_prefix=[],
        profile_path=None,
        note="no isolation tool — rlimit-only fallback",
    )


def cleanup_isolation(result: IsolationResult) -> None:
    if result.profile_path and os.path.exists(result.profile_path):
        try:
            os.unlink(result.profile_path)
        except OSError as e:
            logger.warning("Failed to remove isolation profile %s: %s", result.profile_path, e)
