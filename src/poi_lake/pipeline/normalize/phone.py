"""Phone normalization — VN default region, returns E.164."""

from __future__ import annotations

import logging

import phonenumbers

logger = logging.getLogger(__name__)


class PhoneNormalizer:
    """Parse free-text phone numbers into E.164 format (e.g. ``+84241234567``).

    Returns ``None`` when the input is unparseable or the parsed number is
    not a valid mobile/landline. Treats the default region as ``VN``.
    """

    def __init__(self, default_region: str = "VN") -> None:
        self.default_region = default_region

    def normalize(self, raw: str | None) -> str | None:
        if not raw or not raw.strip():
            return None
        # Strip common separators that confuse the parser on already-clean
        # forms like "024 1234 5678" — phonenumbers handles spaces, but not
        # things like the unicode dot used by some sources.
        cleaned = raw.strip().replace("·", " ")
        try:
            parsed = phonenumbers.parse(cleaned, self.default_region)
        except phonenumbers.NumberParseException as exc:
            logger.debug("phone parse failed for %r: %s", cleaned, exc)
            return None

        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
