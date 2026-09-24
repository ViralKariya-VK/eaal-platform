"""Password hashing for local Student/Professor accounts.

This is a local-first desktop app with no server to authenticate against,
but a shared family/lab computer can still have more than one person using
it, so passwords are salted and hashed rather than stored in the clear.
Uses only the standard library's ``hashlib.pbkdf2_hmac`` (no bcrypt/argon2
dependency) — adequate for gating access on a single machine; this is not
meant to withstand a targeted offline attack the way a real multi-tenant
server's credential store would need to.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_ITERATIONS = 200_000
_ALGORITHM = "sha256"


def hash_password(password: str) -> str:
    """Return a salted hash, formatted as ``salt_hex$hash_hex``."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check ``password`` against a hash previously produced by ``hash_password``."""
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac(_ALGORITHM, password.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)
