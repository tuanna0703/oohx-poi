"""Shared text-normalization helpers — used by name + address + brand."""

from __future__ import annotations

import re

from unidecode import unidecode

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s\-+/&]", re.UNICODE)


def normalize_text(text: str, *, ascii_fold: bool = True, drop_punct: bool = True) -> str:
    """Lowercase + collapse whitespace; optionally accent-strip + punctuation-strip.

    Vietnamese-specific guidance:
      * ``ascii_fold=True`` is what the dedupe layer wants (cosine + fuzzy
        match shouldn't treat "phở" and "pho" as different strings).
      * Use ``ascii_fold=False`` when you need to keep diacritics for display
        or for the embedding input — sentence-transformers' multilingual
        MiniLM preserves Vietnamese tone information.
    """
    if not text:
        return ""
    s = text.strip().lower()
    if drop_punct:
        s = _PUNCT_RE.sub(" ", s)
    if ascii_fold:
        s = unidecode(s)
    return _WS_RE.sub(" ", s).strip()
