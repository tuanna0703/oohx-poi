"""Sample Overpass API responses (subset of the real shape)."""

from __future__ import annotations

NEARBY_RESPONSE: dict = {
    "version": 0.6,
    "generator": "Overpass API 0.7.62",
    "elements": [
        {
            "type": "node",
            "id": 12345,
            "lat": 21.0285,
            "lon": 105.8542,
            "tags": {
                "amenity": "cafe",
                "name": "Cộng Cà Phê — Phan Đình Phùng",
                "addr:street": "Phan Đình Phùng",
                "addr:city": "Hà Nội",
                "phone": "+84 90 123 4567",
            },
        },
        {
            "type": "way",
            "id": 67890,
            "center": {"lat": 21.0290, "lon": 105.8550},
            "tags": {
                "amenity": "restaurant",
                "name": "Quán Ăn Ngon",
                "addr:street": "1 Phan Bội Châu",
            },
        },
        {
            "type": "node",
            "id": 11111,
            "lat": 21.0260,
            "lon": 105.8530,
            "tags": {
                "shop": "convenience",
                "name": "VinMart+",
                "brand": "VinMart+",
            },
        },
    ],
}
