"""API key generation + verification."""

from __future__ import annotations

from poi_lake.services.api_keys import generate_api_key, hash_api_key, verify_api_key


def test_generated_key_has_prefix_and_unique() -> None:
    a = generate_api_key()
    b = generate_api_key()
    assert a.plaintext.startswith("pl_")
    assert a.plaintext != b.plaintext
    assert a.hash != b.hash
    # Hash is hex SHA-256 → 64 hex chars
    assert len(a.hash) == 64
    assert all(c in "0123456789abcdef" for c in a.hash)


def test_verify_correct_key() -> None:
    k = generate_api_key()
    assert verify_api_key(k.plaintext, k.hash) is True


def test_verify_wrong_key() -> None:
    k = generate_api_key()
    assert verify_api_key(k.plaintext + "x", k.hash) is False
    assert verify_api_key("not-a-key", k.hash) is False


def test_hash_is_deterministic() -> None:
    assert hash_api_key("same-input") == hash_api_key("same-input")
    assert hash_api_key("a") != hash_api_key("b")
