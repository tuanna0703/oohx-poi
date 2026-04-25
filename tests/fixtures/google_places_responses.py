"""Sample Google Places API (New) responses, copied from the live shape."""

from __future__ import annotations

NEARBY_RESPONSE: dict = {
    "places": [
        {
            "id": "ChIJxxxxx-place-1",
            "displayName": {"text": "Highlands Coffee — Tràng Tiền", "languageCode": "vi"},
            "formattedAddress": "1 Tràng Tiền, Hoàn Kiếm, Hà Nội, Việt Nam",
            "location": {"latitude": 21.0247, "longitude": 105.8556},
            "types": ["cafe", "food", "establishment"],
            "primaryType": "cafe",
            "nationalPhoneNumber": "024 1234 5678",
            "internationalPhoneNumber": "+84 24 1234 5678",
            "websiteUri": "https://highlandscoffee.com.vn",
            "rating": 4.4,
            "userRatingCount": 1532,
        },
        {
            "id": "ChIJyyyyy-place-2",
            "displayName": {"text": "Circle K — Bà Triệu", "languageCode": "vi"},
            "formattedAddress": "20 Bà Triệu, Hoàn Kiếm, Hà Nội, Việt Nam",
            "location": {"latitude": 21.0250, "longitude": 105.8545},
            "types": ["convenience_store", "store"],
            "primaryType": "convenience_store",
            "rating": 4.2,
            "userRatingCount": 318,
        },
    ]
}


PLACE_DETAIL_RESPONSE: dict = {
    "id": "ChIJzzzzz-place-3",
    "displayName": {"text": "Phở 24 — Hàng Bài", "languageCode": "vi"},
    "formattedAddress": "16 Hàng Bài, Hoàn Kiếm, Hà Nội",
    "location": {"latitude": 21.0263, "longitude": 105.8550},
    "types": ["restaurant", "food"],
    "primaryType": "restaurant",
    "rating": 4.1,
    "userRatingCount": 870,
}
