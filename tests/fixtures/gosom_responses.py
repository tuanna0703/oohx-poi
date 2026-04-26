"""Sample gosom API responses for unit tests."""

from __future__ import annotations

# Returned by POST /api/v1/jobs
SUBMIT_RESPONSE: dict = {"id": "0d62444f-39d8-4869-8b09-89dee8a6f61c"}

# Returned by GET /api/v1/jobs/{id} once finished
JOB_DONE: dict = {
    "id": "0d62444f-39d8-4869-8b09-89dee8a6f61c",
    "name": "poi-lake-test",
    "date": "2026-04-25T11:00:00Z",
    "status": "ok",
    "data": {
        "keywords": ["circle k"],
        "lang": "vi",
        "zoom": 15,
        "lat": "21.0285",
        "lon": "105.8542",
        "fast_mode": True,
        "radius": 1000,
        "depth": 2,
        "max_time": 600,
    },
}

# Same job earlier in its lifecycle
JOB_RUNNING: dict = {**JOB_DONE, "status": "working"}
JOB_FAILED: dict = {**JOB_DONE, "status": "failed"}

# CSV body returned by /api/v1/jobs/{id}/download — header + 2 rows.
SAMPLE_CSV: str = (
    "input_id,link,title,category,address,phone,website,latitude,longitude,"
    "review_count,review_rating,data_id,place_id\n"
    "ck1,https://maps.google.com/?cid=1,Circle K - Bà Triệu,Cửa hàng tiện lợi,"
    "\"20 Bà Triệu, Hoàn Kiếm, Hà Nội\",1800 6915,https://circlek.com.vn,"
    "21.0250,105.8545,318,4.2,0xabc:0x1,ChIJxxx-1\n"
    "ck2,https://maps.google.com/?cid=2,Circle K - Tràng Tiền,Cửa hàng tiện lợi,"
    "\"35 Tràng Tiền, Hoàn Kiếm, Hà Nội\",,,21.0247,105.8556,158,4.0,"
    "0xabc:0x2,ChIJyyy-2\n"
)

EMPTY_CSV: str = "input_id,link,title,category,address\n"
