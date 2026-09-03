"""AES-256-GCM envelope for encryption-at-rest (G1, HIPAA §164.312 / 等保三级).

When FUSION_SCIENCE_ENCRYPT_AT_REST=1, audit JSON files are written encrypted
and read back decrypted transparently. The 256-bit key is derived (PBKDF2-HMAC-
SHA256, 200k iterations) from FUSION_SCIENCE_ENCRYPTION_KEY (env) or the macOS
Keychain entry "fusion-science/encryption-key" (auto-generated + stored on first
use). Default off — preserves local-first simplicity; enable for HIPAA/等保三级
disk-encryption control.

Envelope format (binary): nonce(12) || ciphertext || tag(16). The Python
cryptography package (lazy import) provides AESGCM, which appends the tag to
the ciphertext, so a stored blob is nonce || AESGCM.encrypt(plaintext).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import secrets

logger = logging.getLogger(__name__)

_KEY_LEN = 32  # AES-256
_DERIV_ITER = 200_000
_MAGIC = b"FS1"  # envelope magic so we never misread plaintext as ciphertext


def _resolve_key() -> bytes | None:
    # Env wins; fall back to Keychain (macOS) where the key is generated on
    # first use. Returns None when no key is provisioned -> caller skips
    # encryption (graceful: encrypt_at_rest flag without a key logs + degrades
    # to plaintext rather than crashing startup).
    raw = os.getenv("FUSION_SCIENCE_ENCRYPTION_KEY", "")
    if raw:
        return hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), b"fusion-science", _DERIV_ITER, _KEY_LEN)
    with contextlib.suppress(Exception):
        from .keychain import get_key, store_key

        key_name = "encryption-key"
        existing = get_key(key_name)
        if not existing:
            existing = secrets.token_urlsafe(32)
            store_key(key_name, existing)
            logger.info("Generated + stored encryption key in Keychain (%s)", key_name)
        return hashlib.pbkdf2_hmac("sha256", existing.encode("utf-8"), b"fusion-science", _DERIV_ITER, _KEY_LEN)
    logger.warning("FUSION_SCIENCE_ENCRYPT_AT_REST set but no key (env/Keychain); writing plaintext")
    return None


def encrypt_bytes(plaintext: bytes) -> bytes:
    # Returns plaintext unchanged when no key. Envelope: MAGIC || nonce || ct.
    key = _resolve_key()
    if key is None:
        return plaintext
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        logger.warning(
            "cryptography not installed; encrypt_at_rest degraded to plaintext. pip install 'fusion-science[security]'"
        )
        return plaintext
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return _MAGIC + nonce + ct


def decrypt_bytes(blob: bytes) -> bytes:
    # Inverse of encrypt_bytes. A non-encrypted (plaintext) input is returned
    # as-is — enables toggling the flag on an existing plaintext audit store
    # without re-encrypting history (new writes encrypt; old reads still work).
    if not blob.startswith(_MAGIC):
        return blob
    key = _resolve_key()
    if key is None:
        logger.error("Encrypted blob present but no decryption key available")
        raise RuntimeError("audit blob is encrypted but no encryption key is provisioned")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise RuntimeError("cryptography not installed; cannot decrypt audit blob") from e
    nonce = blob[len(_MAGIC) : len(_MAGIC) + 12]
    ct = blob[len(_MAGIC) + 12 :]
    return AESGCM(key).decrypt(nonce, ct, None)
