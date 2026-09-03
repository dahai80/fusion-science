"""RFC 6238 TOTP second factor (G6, 等保三级 / HIPAA ePHI).

stdlib-only (hmac/hashlib/base64/struct/time) — no external dependency, so MFA
works on a default install. Per-subject TOTP secrets are provisioned by the
operator in a file pointed at by FUSION_SCIENCE_MFA_SECRETS_FILE
(subject:base32secret per line), the same newline/rotate pattern as the API-key
file. When FUSION_SCIENCE_MFA_REQUIRED=1, /auth/token must additionally carry a
`totp` field verified here; a missing/wrong code is 401.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import struct
import time

logger = logging.getLogger(__name__)

_TOTP_STEP = 30
_TOTP_DIGITS = 6


def _b32_decode(secret: str) -> bytes:
    # RFC 4648 base32, case-insensitive, padding-tolerant.
    s = secret.strip().replace(" ", "").upper()
    pad = (-len(s)) % 8
    return base64.b32decode(s + "=" * pad)


def generate_totp(secret: str, at: float | None = None, step: int = _TOTP_STEP) -> str:
    # HOTP(secret, counter=floor(t/step)), 6 digits.
    key = _b32_decode(secret)
    counter = int((at if at is not None else time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**_TOTP_DIGITS)
    return str(code).zfill(_TOTP_DIGITS)


def verify_totp(secret: str, code: str | None, at: float | None = None) -> bool:
    # Allow ±1 step (30s) drift for clock skew; constant-time compare.
    if not code or not secret:
        return False
    t = at if at is not None else time.time()
    for delta in (0, -1, 1):
        expected = generate_totp(secret, at=t + delta * _TOTP_STEP)
        if hmac.compare_digest(expected, code):
            return True
    return False


def load_mfa_secrets() -> dict[str, str]:
    # subject -> base32 secret. Reads FUSION_SCIENCE_MFA_SECRETS_FILE each call
    # (same live-rotate contract as load_api_keys). Missing file = empty map,
    # which means MFA-required + no secrets -> every /auth/token is rejected
    # (fail-closed: better no access than single-factor when MFA is mandated).
    path = os.getenv("FUSION_SCIENCE_MFA_SECRETS_FILE", "")
    secrets_map: dict[str, str] = {}
    if not path or not os.path.exists(path):
        return secrets_map
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    # rsplit: the subject may itself contain a colon (e.g. the
                    # default "apikey:<key-prefix>" subject), so the secret is
                    # the segment after the LAST colon, not the first.
                    sub, sec = line.rsplit(":", 1)
                    secrets_map[sub.strip()] = sec.strip()
    except OSError as e:
        logger.warning("Failed to read MFA secrets file %s: %s", path, e)
    return secrets_map


def mfa_required() -> bool:
    return os.getenv("FUSION_SCIENCE_MFA_REQUIRED", "").lower() in ("true", "1", "yes")


def verify_subject_mfa(subject: str, code: str | None) -> bool:
    # Returns True when MFA passes OR MFA is not required. Fail-closed when
    # MFA is required but no secret is provisioned for the subject.
    if not mfa_required():
        return True
    secrets_map = load_mfa_secrets()
    secret = secrets_map.get(subject)
    if not secret:
        logger.warning("MFA required but no secret for subject=%s; rejecting (fail-closed)", subject)
        return False
    if not verify_totp(secret, code):
        logger.warning("MFA TOTP verification failed for subject=%s", subject)
        return False
    return True
