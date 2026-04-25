"""Vietnam address parsing + normalization.

VN addresses follow a typical pattern from finest → coarsest:

    <house_number> <street>, <ward>, <district>, <province>, <country>

We split by commas and label tokens by lightweight heuristics (prefixes
like ``P.``, ``Q.``, ``TP.``, ``Tỉnh``, etc.). When the heuristics can't
decide, tokens fall through to the ``unknown`` bucket — the LLM fallback
(Phase 3.5, Claude Sonnet) will resolve those, but rule-based gets us
~80% on real Google + OSM data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from poi_lake.pipeline.normalize.text import normalize_text


@dataclass(slots=True, frozen=True)
class AddressComponents:
    house_number: str | None = None
    street: str | None = None
    ward: str | None = None
    district: str | None = None
    province: str | None = None
    country: str | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, str | float | None]:
        return asdict(self)


# --- prefix taxonomy --------------------------------------------------------

# Each tuple: (prefix_regex, component_label).
# Order matters — longer/more specific prefixes first.
_PREFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^t[hỉ]nh\b\.?\s*", re.IGNORECASE), "province"),
    (re.compile(r"^tp\.?\s*hcm$", re.IGNORECASE), "province_hcm"),
    (re.compile(r"^tp\.?\s*", re.IGNORECASE), "province"),
    (re.compile(r"^th[àa]nh\s*ph[ốo]\b\.?\s*", re.IGNORECASE), "province"),
    (re.compile(r"^qu[ậa]n\b\.?\s*", re.IGNORECASE), "district"),
    (re.compile(r"^q\.?\s*\d+$", re.IGNORECASE), "district"),       # Q.1, Q.3
    (re.compile(r"^q\.\s*", re.IGNORECASE), "district"),
    (re.compile(r"^huy[ệe]n\b\.?\s*", re.IGNORECASE), "district"),
    (re.compile(r"^h\.\s*", re.IGNORECASE), "district"),
    (re.compile(r"^th[ịi]\s*x[ãa]\b\.?\s*", re.IGNORECASE), "district"),
    (re.compile(r"^ph[ưu][ờơ]ng\b\.?\s*", re.IGNORECASE), "ward"),
    (re.compile(r"^p\.?\s*\d+$", re.IGNORECASE), "ward"),           # P.1
    (re.compile(r"^p\.\s*", re.IGNORECASE), "ward"),
    (re.compile(r"^x[ãa]\b\.?\s*", re.IGNORECASE), "ward"),
    (re.compile(r"^th[ịi]\s*tr[ấâ]n\b\.?\s*", re.IGNORECASE), "ward"),
)

_COUNTRY_TOKENS: frozenset[str] = frozenset(
    {"việt nam", "viet nam", "vietnam", "vn"}
)

# Known top-level provinces — for fallback labelling when no prefix is present.
# Lowercased + accent-folded form.
_PROVINCES_FOLDED: frozenset[str] = frozenset(
    {
        "ha noi", "hai phong", "ho chi minh", "tphcm", "hcm", "tp hcm", "tp ho chi minh",
        "da nang", "can tho", "thanh hoa", "nghe an", "ha tinh", "quang binh",
        "quang tri", "thua thien hue", "quang nam", "quang ngai", "binh dinh",
        "phu yen", "khanh hoa", "ninh thuan", "binh thuan", "kon tum", "gia lai",
        "dak lak", "dak nong", "lam dong", "binh phuoc", "tay ninh", "binh duong",
        "dong nai", "ba ria vung tau", "long an", "tien giang", "ben tre",
        "tra vinh", "vinh long", "dong thap", "an giang", "kien giang", "hau giang",
        "soc trang", "bac lieu", "ca mau", "lao cai", "yen bai", "dien bien",
        "lai chau", "son la", "hoa binh", "ha giang", "cao bang", "lang son",
        "tuyen quang", "thai nguyen", "phu tho", "vinh phuc", "bac ninh",
        "bac giang", "quang ninh", "hai duong", "hung yen", "thai binh",
        "ha nam", "nam dinh", "ninh binh",
    }
)


_HOUSE_NUMBER_RE = re.compile(r"^\s*(\d+[a-z]?(?:[-/]\d+[a-z]?)?)\s+(.*)$", re.IGNORECASE)


class AddressNormalizer:
    """Rule-based VN address parser. Stateless and thread-safe."""

    def normalize(self, address: str) -> tuple[str, AddressComponents]:
        """Return ``(normalized_string, components)``.

        ``normalized_string`` keeps Vietnamese diacritics (sentence-transformers
        likes them); ``components`` use accent-folded values for matching.
        """
        if not address:
            return "", AddressComponents()

        cleaned = re.sub(r"\s+", " ", address.strip())
        tokens = [t.strip() for t in cleaned.split(",") if t.strip()]
        if not tokens:
            return cleaned, AddressComponents()

        labelled: dict[str, str] = {}
        unknown: list[str] = []

        for raw_token in tokens:
            token = raw_token
            label = None

            # Country
            if normalize_text(token) in _COUNTRY_TOKENS:
                labelled["country"] = "Việt Nam"
                continue

            # Prefix rules (e.g. P., Q., TP.)
            for pattern, lbl in _PREFIX_RULES:
                if not pattern.search(token):
                    continue
                if lbl == "province_hcm":
                    # Special-case: any "TP. HCM" / "TP.HCM" / "TPHCM" form
                    # always normalizes to the canonical name.
                    labelled["province"] = "Hồ Chí Minh"
                    label = lbl
                    break
                cleaned_value = pattern.sub("", token).strip()
                if cleaned_value:
                    labelled[lbl] = cleaned_value
                    label = lbl
                    break

            if label is not None:
                continue

            # Province by name (no prefix)
            folded = normalize_text(token)
            if folded in _PROVINCES_FOLDED and "province" not in labelled:
                labelled["province"] = token
                continue

            unknown.append(token)

        # If we have unknowns, the first is usually the street (with optional
        # house number). Anything after that goes to ward → district fallthrough.
        if unknown:
            head = unknown[0]
            m = _HOUSE_NUMBER_RE.match(head)
            if m:
                labelled.setdefault("house_number", m.group(1))
                labelled.setdefault("street", m.group(2).strip())
            else:
                labelled.setdefault("street", head)

            remaining = unknown[1:]
            if remaining and "ward" not in labelled:
                labelled["ward"] = remaining.pop(0)
            if remaining and "district" not in labelled:
                labelled["district"] = remaining.pop(0)
            if remaining and "province" not in labelled:
                labelled["province"] = remaining.pop(0)

        components = AddressComponents(
            house_number=labelled.get("house_number"),
            street=labelled.get("street"),
            ward=labelled.get("ward"),
            district=labelled.get("district"),
            province=labelled.get("province"),
            country=labelled.get("country"),
            confidence=self._confidence(labelled),
        )
        normalized_string = self._render(components)
        return normalized_string, components

    @staticmethod
    def _confidence(labelled: dict[str, str]) -> float:
        # Confidence rises with each labelled component. Province is the
        # most informative single field; full-stack fills get ~1.0.
        weights = {
            "house_number": 0.05,
            "street": 0.20,
            "ward": 0.20,
            "district": 0.25,
            "province": 0.25,
            "country": 0.05,
        }
        score = sum(w for k, w in weights.items() if labelled.get(k))
        return round(min(score, 1.0), 2)

    @staticmethod
    def _render(c: AddressComponents) -> str:
        head = " ".join(p for p in (c.house_number, c.street) if p) or None
        ward = f"Phường {c.ward}" if c.ward and not c.ward.lower().startswith(("phường", "p.")) else c.ward
        district = (
            f"Quận {c.district}"
            if c.district and not c.district.lower().startswith(("quận", "q.", "huyện", "h."))
            else c.district
        )
        province = c.province
        country = c.country
        return ", ".join(p for p in (head, ward, district, province, country) if p)
