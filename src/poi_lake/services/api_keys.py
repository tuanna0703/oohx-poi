"""API key generation + verification.

Storage rule: only the SHA-256 hash of a key lives in ``api_clients``. The
plaintext is shown to the operator exactly once (at create time) and never
again — we treat it like a credential, not a config knob.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


_KEY_PREFIX = "pl_"     # makes leaked keys easy to grep for
_KEY_BYTES = 32         # ~256 bits of entropy after URL-safe encoding


@dataclass(slots=True, frozen=True)
class GeneratedKey:
    plaintext: str   # show once, never store
    hash: str        # SHA-256 hex; goes into api_clients.api_key_hash


def generate_api_key() -> GeneratedKey:
    raw = secrets.token_urlsafe(_KEY_BYTES)
    plaintext = _KEY_PREFIX + raw
    return GeneratedKey(plaintext=plaintext, hash=hash_api_key(plaintext))


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_api_key(plaintext: str, expected_hash: str) -> bool:
    """Constant-time compare — never short-circuit on a wrong byte."""
    return secrets.compare_digest(hash_api_key(plaintext), expected_hash)
