from __future__ import annotations

import contextlib
import logging
import subprocess

logger = logging.getLogger(__name__)

_SERVICE_NAME = "fusion-science"


def _security_cmd(args: list[str]) -> str:
    cmd = ["security"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "could not be found" in stderr.lower() or "item could not be found" in stderr.lower():
                raise KeyError(stderr)
            raise RuntimeError(stderr)
        return result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("macOS 'security' command not available") from None


def store_key(key_name: str, value: str) -> bool:
    with contextlib.suppress(KeyError, RuntimeError):
        _security_cmd(["delete-generic-password", "-s", _SERVICE_NAME, "-a", key_name])
    try:
        _security_cmd(
            [
                "add-generic-password",
                "-s",
                _SERVICE_NAME,
                "-a",
                key_name,
                "-w",
                value,
            ]
        )
        logger.info("Stored key '%s' in Keychain", key_name)
        return True
    except Exception as e:
        logger.error("Failed to store key '%s': %s", key_name, e)
        return False


def retrieve_key(key_name: str) -> str | None:
    try:
        result = _security_cmd(
            [
                "find-generic-password",
                "-s",
                _SERVICE_NAME,
                "-a",
                key_name,
                "-w",
            ]
        )
        return result
    except KeyError:
        logger.debug("Key '%s' not found in Keychain", key_name)
        return None
    except Exception as e:
        logger.error("Failed to retrieve key '%s': %s", key_name, e)
        return None


def delete_key(key_name: str) -> bool:
    try:
        _security_cmd(["delete-generic-password", "-s", _SERVICE_NAME, "-a", key_name])
        logger.info("Deleted key '%s' from Keychain", key_name)
        return True
    except KeyError:
        return False
    except Exception as e:
        logger.error("Failed to delete key '%s': %s", key_name, e)
        return False


def list_keys() -> list[str]:
    try:
        result = _security_cmd(["dump-keychain"])
        keys = []
        for line in result.splitlines():
            if '"acct"' in line:
                parts = line.split('"acct"<blob>=')
                if len(parts) > 1:
                    acct = parts[1].strip().strip('"')
                    keys.append(acct)
        return keys
    except Exception as e:
        logger.error("Failed to list keys: %s", e)
        return []


class SecureConfig:
    def __init__(self):
        self._fallback: dict[str, str] = {}

    def store(self, key: str, value: str) -> bool:
        ok = store_key(key, value)
        if not ok:
            self._fallback[key] = value
            logger.warning("Keychain store failed for '%s', using in-memory fallback", key)
        return ok

    def retrieve(self, key: str) -> str | None:
        val = retrieve_key(key)
        if val is not None:
            return val
        return self._fallback.get(key)

    def delete(self, key: str) -> bool:
        self._fallback.pop(key, None)
        return delete_key(key)

    def list_stored_keys(self) -> list[str]:
        kc_keys = list_keys()
        mem_keys = list(self._fallback.keys())
        return list(set(kc_keys + mem_keys))
