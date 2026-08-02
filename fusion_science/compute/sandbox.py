from __future__ import annotations

import ast
import contextlib
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    timeout: int = 120
    max_memory_mb: int = 2048
    max_cpu_seconds: int = 60
    max_processes: int = 50
    allowed_imports: list[str] = field(default_factory=lambda: [
        "numpy", "pandas", "scipy", "matplotlib", "seaborn",
        "sklearn", "statsmodels", "bio", "Bio", "rdkit",
        "pymol", "py3Dmol", "json", "csv", "math",
        "collections", "itertools", "functools", "re",
        "datetime", "pathlib", "typing", "dataclasses",
        "logging", "io", "copy", "operator",
    ])
    blocked_imports: list[str] = field(default_factory=lambda: [
        "subprocess", "os.system", "shutil", "signal",
        "ctypes", "multiprocessing", "threading",
        "socket", "http", "urllib", "requests",
        "ftplib", "smtplib", "telnetlib",
        "pickle", "shelve", "marshal",
    ])


_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"os\.system\s*\(", "os.system() call — use subprocess with validated input"),
    (r"subprocess\.", "subprocess module — restricted for security"),
    (r"\beval\s*\(", "eval() — arbitrary code execution risk"),
    (r"\bexec\s*\(", "exec() — arbitrary code execution risk"),
    (r"__import__\s*\(", "__import__() — bypasses import controls"),
    (r"open\s*\([^)]*['\"]w", "file write — may write outside work_dir"),
    (r"open\s*\([^)]*['\"]a", "file append — may write outside work_dir"),
    (r"compile\s*\(", "compile() — dynamic code generation"),
    (r"os\.remove\s*\(", "os.remove() — file deletion risk"),
    (r"os\.unlink\s*\(", "os.unlink() — file deletion risk"),
    (r"shutil\.rmtree\s*\(", "shutil.rmtree() — recursive directory deletion"),
]


class SandboxManager:

    def __init__(self, config: SandboxConfig | None = None):
        self._config = config or SandboxConfig()
        self._sandboxes: dict[str, dict] = {}
        logger.info(
            "SandboxManager initialized: timeout=%d, max_memory=%dMB, max_cpu=%ds",
            self._config.timeout, self._config.max_memory_mb, self._config.max_cpu_seconds,
        )

    def create_sandbox(self, config: SandboxConfig | None = None) -> dict:
        cfg = config or self._config
        sandbox_id = str(uuid.uuid4())[:12]
        work_dir = tempfile.mkdtemp(prefix=f"fusion_sb_{sandbox_id}_")

        env_vars = {
            "FUSION_SANDBOX_ID": sandbox_id,
            "FUSION_SANDBOX_WORK_DIR": work_dir,
            "FUSION_SANDBOX_TIMEOUT": str(cfg.timeout),
            "FUSION_SANDBOX_MAX_MEMORY_MB": str(cfg.max_memory_mb),
            "FUSION_SANDBOX_MAX_CPU_S": str(cfg.max_cpu_seconds),
            "MPLCONFIGDIR": os.path.join(work_dir, ".matplotlib"),
            "HOME": work_dir,
            "TMPDIR": os.path.join(work_dir, "tmp"),
        }

        os.makedirs(os.path.join(work_dir, "tmp"), exist_ok=True)
        os.makedirs(os.path.join(work_dir, ".matplotlib"), exist_ok=True)

        sandbox_info = {
            "sandbox_id": sandbox_id,
            "work_dir": work_dir,
            "env_vars": env_vars,
            "config": cfg,
            "status": "created",
        }
        self._sandboxes[sandbox_id] = sandbox_info

        logger.info("Created sandbox %s at %s", sandbox_id, work_dir)
        return {
            "sandbox_id": sandbox_id,
            "work_dir": work_dir,
            "env_vars": env_vars,
        }

    def cleanup_sandbox(self, sandbox_id: str) -> bool:
        if sandbox_id not in self._sandboxes:
            logger.warning("cleanup_sandbox: unknown sandbox %s", sandbox_id)
            return False

        info = self._sandboxes.pop(sandbox_id)
        work_dir = info.get("work_dir", "")

        if work_dir and os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir)
                logger.info("Cleaned up sandbox %s, removed %s", sandbox_id, work_dir)
            except Exception as e:
                logger.error("Failed to remove sandbox dir %s: %s", work_dir, e)
                return False
        else:
            logger.warning("Sandbox %s work_dir missing or already removed: %s", sandbox_id, work_dir)

        return True

    def validate_code(self, code: str, language: str = "python") -> dict:
        issues = []
        risk_level = "low"

        if language.lower() != "python":
            return {"valid": True, "issues": [], "risk_level": "low"}

        # AST-based checks
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {"valid": False, "issues": [f"Syntax error: {e}"], "risk_level": "high"}

        import_issues = self._check_imports(tree)
        issues.extend(import_issues)

        call_issues = self._check_dangerous_calls(tree)
        issues.extend(call_issues)

        # Regex-based pattern checks
        import re
        for pattern, description in _BLOCKED_PATTERNS:
            if re.search(pattern, code):
                issues.append(description)

        # File write outside work_dir
        write_issues = self._check_file_writes(tree)
        issues.extend(write_issues)

        if issues:
            high_keywords = ["eval", "exec", "os.system", "subprocess", "__import__", "arbitrary"]
            if any(kw in " ".join(issues).lower() for kw in high_keywords):
                risk_level = "high"
            else:
                risk_level = "medium"

        valid = risk_level != "high"
        logger.info(
            "validate_code: valid=%s risk=%s issues=%d",
            valid, risk_level, len(issues),
        )
        return {"valid": valid, "issues": issues, "risk_level": risk_level}

    def get_resource_usage(self, sandbox_id: str) -> dict:
        if sandbox_id not in self._sandboxes:
            logger.warning("get_resource_usage: unknown sandbox %s", sandbox_id)
            return {"error": f"sandbox {sandbox_id} not found"}

        info = self._sandboxes[sandbox_id]
        work_dir = info.get("work_dir", "")

        usage = {
            "memory_mb": 0,
            "cpu_seconds": 0,
            "processes": 0,
            "work_dir_size_mb": 0.0,
        }

        if work_dir and os.path.isdir(work_dir):
            total_size = 0
            for dirpath, _dirnames, filenames in os.walk(work_dir):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    with contextlib.suppress(OSError):
                        total_size += os.path.getsize(fp)
            usage["work_dir_size_mb"] = round(total_size / (1024 * 1024), 2)

        return usage

    def _check_imports(self, tree: ast.AST) -> list[str]:
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_module = alias.name.split(".")[0]
                    if root_module in self._config.blocked_imports:
                        issues.append(f"Blocked import: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_module = node.module.split(".")[0]
                if root_module in self._config.blocked_imports:
                    issues.append(f"Blocked import: from {node.module}")
        return issues

    def _check_dangerous_calls(self, tree: ast.AST) -> list[str]:
        issues = []
        dangerous_names = {"eval", "exec", "compile", "__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in dangerous_names:
                    issues.append(f"Dangerous call: {func.id}()")
                elif isinstance(func, ast.Attribute):
                    attr_chain = self._get_attr_chain(func)
                    if attr_chain:
                        joined = ".".join(attr_chain)
                        if joined.startswith("os.system"):
                            issues.append(f"Dangerous call: {joined}()")
                        elif joined.startswith("subprocess."):
                            issues.append(f"Restricted call: {joined}()")
                        elif joined.startswith("shutil.rmtree"):
                            issues.append(f"Dangerous call: {joined}()")
        return issues

    @staticmethod
    def _get_attr_chain(node: ast.Attribute) -> list[str] | None:
        parts = [node.attr]
        current = node.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            return parts
        return None

    @staticmethod
    def _check_file_writes(tree: ast.AST) -> list[str]:
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open" and node.args and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                    if "w" in mode or "a" in mode:
                        issues.append("File write detected — ensure writes are within sandbox work_dir")
        return issues

    def cleanup_all(self) -> int:
        count = 0
        for sid in list(self._sandboxes.keys()):
            if self.cleanup_sandbox(sid):
                count += 1
        logger.info("cleanup_all: removed %d sandboxes", count)
        return count
