"""API keys entered in bizmap's admin are stored sealed and never shown back."""

from __future__ import annotations

from poi_lake.services.app_settings import DEFAULTS, public, seal, unseal


def test_seal_round_trips_and_differs_from_the_key() -> None:
    token = seal("sk-test-1234567890")
    assert "sk-test" not in token
    assert unseal(token) == "sk-test-1234567890"
    assert unseal("not-a-token") is None


def test_public_view_has_no_key() -> None:
    settings = {**DEFAULTS, "providers": {
        "claude": {"model": "claude-haiku-5-5", "role": "primary", "key": seal("sk-abcdefWXYZ"), "key_last4": "WXYZ"},
        "openai": {"model": "", "role": "fallback"},
    }}
    view = public(settings)
    claude = {k: v for k, v in view["providers"]["claude"].items() if k != "server_key"}
    assert claude == {
        "model": "claude-haiku-5-5", "role": "primary", "has_key": True, "key_last4": "WXYZ", "checked": None,
    }
    assert "sk-abcdef" not in str(view)
