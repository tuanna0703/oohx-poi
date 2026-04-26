"""Vietnamese administrative-division reference data.

  * 63 provinces / centrally-governed cities (level=1) with bounding boxes.
  * 30 districts of Hà Nội + 22 districts of TP.HCM + 8 districts of Đà Nẵng
    (level=2). Other provinces' districts can be added via the admin API or
    follow-up migrations once a curated dataset is available.
  * Wards (level=3) are NOT seeded here — there are ~11,000 in VN, which
    requires a real GIS dataset import. The schema supports them; pipeline
    leaves ``ward_code`` NULL until population.

Province codes follow the Tổng cục Thống kê (GSO) 2-digit standard;
district codes are ``<province>.<3-digit GSO district code>``. Bboxes are
``[lng_min, lat_min, lng_max, lat_max]`` in WGS84, generous-but-realistic
(slightly larger than the actual boundary so a tile sweep covers the
province with overlap into neighbours — dedupe folds the duplicates).
"""

from __future__ import annotations

from typing import TypedDict


class AdminUnitRow(TypedDict):
    code: str
    name: str
    parent_code: str | None
    level: int
    lng_min: float
    lat_min: float
    lng_max: float
    lat_max: float


# ---- 63 provinces / centrally-governed cities ------------------------------

PROVINCES: list[AdminUnitRow] = [
    # Northern (mountainous + Red River delta)
    {"code": "01", "name": "Hà Nội",          "parent_code": None, "level": 1, "lng_min": 105.30, "lat_min": 20.55, "lng_max": 106.05, "lat_max": 21.40},
    {"code": "02", "name": "Hà Giang",        "parent_code": None, "level": 1, "lng_min": 104.30, "lat_min": 22.10, "lng_max": 105.50, "lat_max": 23.40},
    {"code": "04", "name": "Cao Bằng",        "parent_code": None, "level": 1, "lng_min": 105.50, "lat_min": 22.30, "lng_max": 106.80, "lat_max": 23.30},
    {"code": "06", "name": "Bắc Kạn",         "parent_code": None, "level": 1, "lng_min": 105.40, "lat_min": 21.85, "lng_max": 106.30, "lat_max": 22.80},
    {"code": "08", "name": "Tuyên Quang",     "parent_code": None, "level": 1, "lng_min": 104.80, "lat_min": 21.40, "lng_max": 105.80, "lat_max": 22.80},
    {"code": "10", "name": "Lào Cai",         "parent_code": None, "level": 1, "lng_min": 103.55, "lat_min": 21.85, "lng_max": 104.70, "lat_max": 22.85},
    {"code": "11", "name": "Điện Biên",       "parent_code": None, "level": 1, "lng_min": 102.10, "lat_min": 20.65, "lng_max": 103.70, "lat_max": 22.35},
    {"code": "12", "name": "Lai Châu",        "parent_code": None, "level": 1, "lng_min": 102.80, "lat_min": 21.40, "lng_max": 104.00, "lat_max": 22.80},
    {"code": "14", "name": "Sơn La",          "parent_code": None, "level": 1, "lng_min": 103.10, "lat_min": 20.60, "lng_max": 105.30, "lat_max": 22.10},
    {"code": "15", "name": "Yên Bái",          "parent_code": None, "level": 1, "lng_min": 103.70, "lat_min": 21.20, "lng_max": 105.10, "lat_max": 22.30},
    {"code": "17", "name": "Hòa Bình",        "parent_code": None, "level": 1, "lng_min": 104.65, "lat_min": 20.30, "lng_max": 106.00, "lat_max": 21.10},
    {"code": "19", "name": "Thái Nguyên",     "parent_code": None, "level": 1, "lng_min": 105.50, "lat_min": 21.30, "lng_max": 106.30, "lat_max": 22.05},
    {"code": "20", "name": "Lạng Sơn",        "parent_code": None, "level": 1, "lng_min": 106.00, "lat_min": 21.30, "lng_max": 107.40, "lat_max": 22.50},
    {"code": "22", "name": "Quảng Ninh",      "parent_code": None, "level": 1, "lng_min": 106.50, "lat_min": 20.70, "lng_max": 108.10, "lat_max": 21.70},
    {"code": "24", "name": "Bắc Giang",       "parent_code": None, "level": 1, "lng_min": 105.85, "lat_min": 21.15, "lng_max": 107.10, "lat_max": 21.80},
    {"code": "25", "name": "Phú Thọ",         "parent_code": None, "level": 1, "lng_min": 104.80, "lat_min": 20.80, "lng_max": 105.65, "lat_max": 21.85},
    {"code": "26", "name": "Vĩnh Phúc",       "parent_code": None, "level": 1, "lng_min": 105.15, "lat_min": 21.05, "lng_max": 105.80, "lat_max": 21.55},
    {"code": "27", "name": "Bắc Ninh",        "parent_code": None, "level": 1, "lng_min": 105.85, "lat_min": 20.95, "lng_max": 106.30, "lat_max": 21.30},
    {"code": "30", "name": "Hải Dương",       "parent_code": None, "level": 1, "lng_min": 106.00, "lat_min": 20.55, "lng_max": 106.80, "lat_max": 21.20},
    {"code": "31", "name": "Hải Phòng",       "parent_code": None, "level": 1, "lng_min": 106.40, "lat_min": 20.40, "lng_max": 107.05, "lat_max": 21.10},
    {"code": "33", "name": "Hưng Yên",        "parent_code": None, "level": 1, "lng_min": 105.95, "lat_min": 20.55, "lng_max": 106.40, "lat_max": 21.05},
    {"code": "34", "name": "Thái Bình",       "parent_code": None, "level": 1, "lng_min": 106.05, "lat_min": 20.20, "lng_max": 106.85, "lat_max": 20.80},
    {"code": "35", "name": "Hà Nam",          "parent_code": None, "level": 1, "lng_min": 105.65, "lat_min": 20.30, "lng_max": 106.20, "lat_max": 20.80},
    {"code": "36", "name": "Nam Định",        "parent_code": None, "level": 1, "lng_min": 105.95, "lat_min": 19.95, "lng_max": 106.55, "lat_max": 20.55},
    {"code": "37", "name": "Ninh Bình",       "parent_code": None, "level": 1, "lng_min": 105.35, "lat_min": 19.85, "lng_max": 106.20, "lat_max": 20.45},
    # Northern + Central (transition)
    {"code": "38", "name": "Thanh Hóa",       "parent_code": None, "level": 1, "lng_min": 104.30, "lat_min": 19.20, "lng_max": 106.10, "lat_max": 20.65},
    {"code": "40", "name": "Nghệ An",         "parent_code": None, "level": 1, "lng_min": 103.85, "lat_min": 18.55, "lng_max": 105.85, "lat_max": 20.05},
    {"code": "42", "name": "Hà Tĩnh",         "parent_code": None, "level": 1, "lng_min": 105.10, "lat_min": 17.90, "lng_max": 106.55, "lat_max": 18.85},
    {"code": "44", "name": "Quảng Bình",      "parent_code": None, "level": 1, "lng_min": 105.55, "lat_min": 17.05, "lng_max": 107.00, "lat_max": 18.10},
    {"code": "45", "name": "Quảng Trị",       "parent_code": None, "level": 1, "lng_min": 106.45, "lat_min": 16.40, "lng_max": 107.50, "lat_max": 17.15},
    {"code": "46", "name": "Thừa Thiên Huế",  "parent_code": None, "level": 1, "lng_min": 107.00, "lat_min": 15.95, "lng_max": 108.20, "lat_max": 16.80},
    {"code": "48", "name": "Đà Nẵng",         "parent_code": None, "level": 1, "lng_min": 107.95, "lat_min": 15.95, "lng_max": 108.40, "lat_max": 16.30},
    {"code": "49", "name": "Quảng Nam",       "parent_code": None, "level": 1, "lng_min": 107.20, "lat_min": 14.95, "lng_max": 108.80, "lat_max": 16.20},
    {"code": "51", "name": "Quảng Ngãi",      "parent_code": None, "level": 1, "lng_min": 108.10, "lat_min": 14.30, "lng_max": 109.20, "lat_max": 15.40},
    {"code": "52", "name": "Bình Định",       "parent_code": None, "level": 1, "lng_min": 108.35, "lat_min": 13.55, "lng_max": 109.40, "lat_max": 14.75},
    {"code": "54", "name": "Phú Yên",         "parent_code": None, "level": 1, "lng_min": 108.55, "lat_min": 12.85, "lng_max": 109.50, "lat_max": 13.60},
    {"code": "56", "name": "Khánh Hòa",       "parent_code": None, "level": 1, "lng_min": 108.55, "lat_min": 11.80, "lng_max": 109.70, "lat_max": 12.95},
    {"code": "58", "name": "Ninh Thuận",      "parent_code": None, "level": 1, "lng_min": 108.55, "lat_min": 11.20, "lng_max": 109.30, "lat_max": 12.20},
    {"code": "60", "name": "Bình Thuận",      "parent_code": None, "level": 1, "lng_min": 107.40, "lat_min": 10.55, "lng_max": 109.05, "lat_max": 11.60},
    # Central highlands
    {"code": "62", "name": "Kon Tum",         "parent_code": None, "level": 1, "lng_min": 107.20, "lat_min": 13.85, "lng_max": 108.70, "lat_max": 15.30},
    {"code": "64", "name": "Gia Lai",         "parent_code": None, "level": 1, "lng_min": 107.20, "lat_min": 12.85, "lng_max": 109.00, "lat_max": 14.55},
    {"code": "66", "name": "Đắk Lắk",         "parent_code": None, "level": 1, "lng_min": 107.50, "lat_min": 12.10, "lng_max": 109.00, "lat_max": 13.30},
    {"code": "67", "name": "Đắk Nông",        "parent_code": None, "level": 1, "lng_min": 107.10, "lat_min": 11.65, "lng_max": 108.20, "lat_max": 12.85},
    {"code": "68", "name": "Lâm Đồng",        "parent_code": None, "level": 1, "lng_min": 107.20, "lat_min": 11.10, "lng_max": 108.80, "lat_max": 12.30},
    # Southern (southeast)
    {"code": "70", "name": "Bình Phước",      "parent_code": None, "level": 1, "lng_min": 106.20, "lat_min": 11.20, "lng_max": 107.45, "lat_max": 12.30},
    {"code": "72", "name": "Tây Ninh",        "parent_code": None, "level": 1, "lng_min": 105.85, "lat_min": 10.85, "lng_max": 106.80, "lat_max": 11.85},
    {"code": "74", "name": "Bình Dương",      "parent_code": None, "level": 1, "lng_min": 106.30, "lat_min": 10.85, "lng_max": 107.10, "lat_max": 11.65},
    {"code": "75", "name": "Đồng Nai",        "parent_code": None, "level": 1, "lng_min": 106.65, "lat_min": 10.50, "lng_max": 107.55, "lat_max": 11.55},
    {"code": "77", "name": "Bà Rịa - Vũng Tàu","parent_code": None, "level": 1, "lng_min": 107.00, "lat_min": 10.20, "lng_max": 107.80, "lat_max": 10.85},
    {"code": "79", "name": "Hồ Chí Minh",     "parent_code": None, "level": 1, "lng_min": 106.35, "lat_min": 10.35, "lng_max": 107.05, "lat_max": 11.20},
    # Mekong delta
    {"code": "80", "name": "Long An",         "parent_code": None, "level": 1, "lng_min": 105.45, "lat_min": 10.30, "lng_max": 106.85, "lat_max": 11.10},
    {"code": "82", "name": "Tiền Giang",      "parent_code": None, "level": 1, "lng_min": 105.85, "lat_min": 10.10, "lng_max": 106.85, "lat_max": 10.65},
    {"code": "83", "name": "Bến Tre",         "parent_code": None, "level": 1, "lng_min": 105.95, "lat_min": 9.75,  "lng_max": 106.75, "lat_max": 10.40},
    {"code": "84", "name": "Trà Vinh",        "parent_code": None, "level": 1, "lng_min": 105.95, "lat_min": 9.45,  "lng_max": 106.70, "lat_max": 10.05},
    {"code": "86", "name": "Vĩnh Long",       "parent_code": None, "level": 1, "lng_min": 105.65, "lat_min": 9.85,  "lng_max": 106.30, "lat_max": 10.40},
    {"code": "87", "name": "Đồng Tháp",       "parent_code": None, "level": 1, "lng_min": 105.10, "lat_min": 10.00, "lng_max": 106.05, "lat_max": 10.95},
    {"code": "89", "name": "An Giang",        "parent_code": None, "level": 1, "lng_min": 104.75, "lat_min": 10.10, "lng_max": 105.65, "lat_max": 10.95},
    {"code": "91", "name": "Kiên Giang",      "parent_code": None, "level": 1, "lng_min": 103.40, "lat_min": 9.20,  "lng_max": 105.65, "lat_max": 10.85},
    {"code": "92", "name": "Cần Thơ",         "parent_code": None, "level": 1, "lng_min": 105.45, "lat_min": 9.85,  "lng_max": 105.95, "lat_max": 10.30},
    {"code": "93", "name": "Hậu Giang",       "parent_code": None, "level": 1, "lng_min": 105.30, "lat_min": 9.55,  "lng_max": 106.10, "lat_max": 10.05},
    {"code": "94", "name": "Sóc Trăng",       "parent_code": None, "level": 1, "lng_min": 105.40, "lat_min": 9.10,  "lng_max": 106.40, "lat_max": 9.95},
    {"code": "95", "name": "Bạc Liêu",        "parent_code": None, "level": 1, "lng_min": 105.10, "lat_min": 9.10,  "lng_max": 106.00, "lat_max": 9.70},
    {"code": "96", "name": "Cà Mau",          "parent_code": None, "level": 1, "lng_min": 104.55, "lat_min": 8.45,  "lng_max": 105.55, "lat_max": 9.55},
]

# ---- Districts of Hà Nội (12 inner urban + 17 suburban + 1 town) ----------

HANOI_DISTRICTS: list[AdminUnitRow] = [
    # 12 inner urban quận
    {"code": "01.001", "name": "Ba Đình",     "parent_code": "01", "level": 2, "lng_min": 105.815, "lat_min": 21.020, "lng_max": 105.855, "lat_max": 21.055},
    {"code": "01.002", "name": "Hoàn Kiếm",   "parent_code": "01", "level": 2, "lng_min": 105.840, "lat_min": 21.015, "lng_max": 105.870, "lat_max": 21.045},
    {"code": "01.003", "name": "Tây Hồ",      "parent_code": "01", "level": 2, "lng_min": 105.795, "lat_min": 21.040, "lng_max": 105.865, "lat_max": 21.110},
    {"code": "01.004", "name": "Long Biên",   "parent_code": "01", "level": 2, "lng_min": 105.870, "lat_min": 20.985, "lng_max": 105.985, "lat_max": 21.090},
    {"code": "01.005", "name": "Cầu Giấy",    "parent_code": "01", "level": 2, "lng_min": 105.770, "lat_min": 21.020, "lng_max": 105.815, "lat_max": 21.060},
    {"code": "01.006", "name": "Đống Đa",     "parent_code": "01", "level": 2, "lng_min": 105.810, "lat_min": 21.000, "lng_max": 105.855, "lat_max": 21.030},
    {"code": "01.007", "name": "Hai Bà Trưng","parent_code": "01", "level": 2, "lng_min": 105.840, "lat_min": 20.985, "lng_max": 105.890, "lat_max": 21.020},
    {"code": "01.008", "name": "Hoàng Mai",   "parent_code": "01", "level": 2, "lng_min": 105.825, "lat_min": 20.945, "lng_max": 105.910, "lat_max": 21.000},
    {"code": "01.009", "name": "Thanh Xuân",  "parent_code": "01", "level": 2, "lng_min": 105.785, "lat_min": 20.985, "lng_max": 105.835, "lat_max": 21.020},
    {"code": "01.268", "name": "Hà Đông",     "parent_code": "01", "level": 2, "lng_min": 105.715, "lat_min": 20.940, "lng_max": 105.815, "lat_max": 21.005},
    {"code": "01.269", "name": "Sơn Tây",     "parent_code": "01", "level": 2, "lng_min": 105.430, "lat_min": 21.060, "lng_max": 105.555, "lat_max": 21.180},
    {"code": "01.018", "name": "Bắc Từ Liêm", "parent_code": "01", "level": 2, "lng_min": 105.715, "lat_min": 21.045, "lng_max": 105.800, "lat_max": 21.110},
    {"code": "01.019", "name": "Nam Từ Liêm", "parent_code": "01", "level": 2, "lng_min": 105.715, "lat_min": 20.985, "lng_max": 105.800, "lat_max": 21.045},
    # 17 huyện ngoại thành
    {"code": "01.250", "name": "Sóc Sơn",     "parent_code": "01", "level": 2, "lng_min": 105.700, "lat_min": 21.140, "lng_max": 105.965, "lat_max": 21.350},
    {"code": "01.271", "name": "Đông Anh",    "parent_code": "01", "level": 2, "lng_min": 105.770, "lat_min": 21.080, "lng_max": 105.945, "lat_max": 21.220},
    {"code": "01.272", "name": "Gia Lâm",     "parent_code": "01", "level": 2, "lng_min": 105.890, "lat_min": 20.965, "lng_max": 106.040, "lat_max": 21.090},
    {"code": "01.273", "name": "Mê Linh",     "parent_code": "01", "level": 2, "lng_min": 105.620, "lat_min": 21.140, "lng_max": 105.825, "lat_max": 21.290},
    {"code": "01.274", "name": "Thanh Trì",   "parent_code": "01", "level": 2, "lng_min": 105.810, "lat_min": 20.870, "lng_max": 105.965, "lat_max": 20.965},
    {"code": "01.275", "name": "Ba Vì",       "parent_code": "01", "level": 2, "lng_min": 105.290, "lat_min": 21.040, "lng_max": 105.520, "lat_max": 21.330},
    {"code": "01.276", "name": "Phúc Thọ",    "parent_code": "01", "level": 2, "lng_min": 105.475, "lat_min": 21.020, "lng_max": 105.620, "lat_max": 21.180},
    {"code": "01.277", "name": "Đan Phượng",  "parent_code": "01", "level": 2, "lng_min": 105.605, "lat_min": 21.040, "lng_max": 105.730, "lat_max": 21.165},
    {"code": "01.278", "name": "Hoài Đức",    "parent_code": "01", "level": 2, "lng_min": 105.650, "lat_min": 20.965, "lng_max": 105.755, "lat_max": 21.080},
    {"code": "01.279", "name": "Quốc Oai",    "parent_code": "01", "level": 2, "lng_min": 105.500, "lat_min": 20.910, "lng_max": 105.715, "lat_max": 21.080},
    {"code": "01.280", "name": "Thạch Thất",  "parent_code": "01", "level": 2, "lng_min": 105.420, "lat_min": 20.945, "lng_max": 105.640, "lat_max": 21.130},
    {"code": "01.281", "name": "Chương Mỹ",   "parent_code": "01", "level": 2, "lng_min": 105.585, "lat_min": 20.770, "lng_max": 105.785, "lat_max": 20.965},
    {"code": "01.282", "name": "Thanh Oai",   "parent_code": "01", "level": 2, "lng_min": 105.685, "lat_min": 20.815, "lng_max": 105.840, "lat_max": 20.965},
    {"code": "01.283", "name": "Thường Tín",  "parent_code": "01", "level": 2, "lng_min": 105.815, "lat_min": 20.755, "lng_max": 105.965, "lat_max": 20.890},
    {"code": "01.284", "name": "Phú Xuyên",   "parent_code": "01", "level": 2, "lng_min": 105.770, "lat_min": 20.625, "lng_max": 105.940, "lat_max": 20.785},
    {"code": "01.285", "name": "Ứng Hòa",     "parent_code": "01", "level": 2, "lng_min": 105.620, "lat_min": 20.620, "lng_max": 105.815, "lat_max": 20.815},
    {"code": "01.286", "name": "Mỹ Đức",      "parent_code": "01", "level": 2, "lng_min": 105.560, "lat_min": 20.555, "lng_max": 105.795, "lat_max": 20.785},
]


# ---- Districts of TP. Hồ Chí Minh (16 quận + 5 huyện + 1 TP. Thủ Đức) ----

HCMC_DISTRICTS: list[AdminUnitRow] = [
    {"code": "79.760", "name": "Quận 1",       "parent_code": "79", "level": 2, "lng_min": 106.685, "lat_min": 10.760, "lng_max": 106.715, "lat_max": 10.795},
    {"code": "79.761", "name": "Quận 2",       "parent_code": "79", "level": 2, "lng_min": 106.715, "lat_min": 10.760, "lng_max": 106.795, "lat_max": 10.825},
    {"code": "79.764", "name": "Quận 3",       "parent_code": "79", "level": 2, "lng_min": 106.670, "lat_min": 10.770, "lng_max": 106.700, "lat_max": 10.795},
    {"code": "79.766", "name": "Quận 4",       "parent_code": "79", "level": 2, "lng_min": 106.690, "lat_min": 10.745, "lng_max": 106.715, "lat_max": 10.770},
    {"code": "79.767", "name": "Quận 5",       "parent_code": "79", "level": 2, "lng_min": 106.655, "lat_min": 10.750, "lng_max": 106.685, "lat_max": 10.775},
    {"code": "79.768", "name": "Quận 6",       "parent_code": "79", "level": 2, "lng_min": 106.625, "lat_min": 10.735, "lng_max": 106.665, "lat_max": 10.770},
    {"code": "79.769", "name": "Quận 7",       "parent_code": "79", "level": 2, "lng_min": 106.690, "lat_min": 10.700, "lng_max": 106.770, "lat_max": 10.760},
    {"code": "79.770", "name": "Quận 8",       "parent_code": "79", "level": 2, "lng_min": 106.625, "lat_min": 10.715, "lng_max": 106.700, "lat_max": 10.760},
    {"code": "79.771", "name": "Quận 9",       "parent_code": "79", "level": 2, "lng_min": 106.745, "lat_min": 10.825, "lng_max": 106.890, "lat_max": 10.910},
    {"code": "79.772", "name": "Quận 10",      "parent_code": "79", "level": 2, "lng_min": 106.660, "lat_min": 10.765, "lng_max": 106.685, "lat_max": 10.785},
    {"code": "79.773", "name": "Quận 11",      "parent_code": "79", "level": 2, "lng_min": 106.640, "lat_min": 10.755, "lng_max": 106.665, "lat_max": 10.780},
    {"code": "79.774", "name": "Quận 12",      "parent_code": "79", "level": 2, "lng_min": 106.605, "lat_min": 10.825, "lng_max": 106.685, "lat_max": 10.890},
    {"code": "79.775", "name": "Bình Thạnh",   "parent_code": "79", "level": 2, "lng_min": 106.685, "lat_min": 10.785, "lng_max": 106.730, "lat_max": 10.825},
    {"code": "79.776", "name": "Tân Bình",     "parent_code": "79", "level": 2, "lng_min": 106.625, "lat_min": 10.770, "lng_max": 106.685, "lat_max": 10.815},
    {"code": "79.777", "name": "Tân Phú",      "parent_code": "79", "level": 2, "lng_min": 106.610, "lat_min": 10.760, "lng_max": 106.660, "lat_max": 10.810},
    {"code": "79.778", "name": "Phú Nhuận",    "parent_code": "79", "level": 2, "lng_min": 106.665, "lat_min": 10.785, "lng_max": 106.695, "lat_max": 10.810},
    {"code": "79.779", "name": "Thủ Đức",      "parent_code": "79", "level": 2, "lng_min": 106.715, "lat_min": 10.760, "lng_max": 106.890, "lat_max": 10.910},
    {"code": "79.780", "name": "Gò Vấp",       "parent_code": "79", "level": 2, "lng_min": 106.640, "lat_min": 10.815, "lng_max": 106.700, "lat_max": 10.870},
    {"code": "79.783", "name": "Bình Tân",     "parent_code": "79", "level": 2, "lng_min": 106.580, "lat_min": 10.715, "lng_max": 106.640, "lat_max": 10.795},
    {"code": "79.784", "name": "Củ Chi",       "parent_code": "79", "level": 2, "lng_min": 106.380, "lat_min": 10.945, "lng_max": 106.615, "lat_max": 11.140},
    {"code": "79.785", "name": "Hóc Môn",      "parent_code": "79", "level": 2, "lng_min": 106.530, "lat_min": 10.825, "lng_max": 106.680, "lat_max": 10.945},
    {"code": "79.786", "name": "Bình Chánh",   "parent_code": "79", "level": 2, "lng_min": 106.430, "lat_min": 10.605, "lng_max": 106.680, "lat_max": 10.795},
    {"code": "79.787", "name": "Nhà Bè",       "parent_code": "79", "level": 2, "lng_min": 106.660, "lat_min": 10.585, "lng_max": 106.785, "lat_max": 10.730},
    {"code": "79.788", "name": "Cần Giờ",      "parent_code": "79", "level": 2, "lng_min": 106.700, "lat_min": 10.300, "lng_max": 107.020, "lat_max": 10.640},
]


# ---- Districts of Đà Nẵng (6 quận + 2 huyện) ------------------------------

DA_NANG_DISTRICTS: list[AdminUnitRow] = [
    {"code": "48.490", "name": "Hải Châu",    "parent_code": "48", "level": 2, "lng_min": 108.190, "lat_min": 16.040, "lng_max": 108.245, "lat_max": 16.085},
    {"code": "48.491", "name": "Thanh Khê",   "parent_code": "48", "level": 2, "lng_min": 108.170, "lat_min": 16.040, "lng_max": 108.210, "lat_max": 16.080},
    {"code": "48.492", "name": "Sơn Trà",     "parent_code": "48", "level": 2, "lng_min": 108.220, "lat_min": 16.060, "lng_max": 108.330, "lat_max": 16.155},
    {"code": "48.493", "name": "Ngũ Hành Sơn","parent_code": "48", "level": 2, "lng_min": 108.225, "lat_min": 15.965, "lng_max": 108.290, "lat_max": 16.060},
    {"code": "48.494", "name": "Liên Chiểu",  "parent_code": "48", "level": 2, "lng_min": 108.090, "lat_min": 16.040, "lng_max": 108.180, "lat_max": 16.155},
    {"code": "48.495", "name": "Cẩm Lệ",      "parent_code": "48", "level": 2, "lng_min": 108.155, "lat_min": 15.995, "lng_max": 108.230, "lat_max": 16.045},
    {"code": "48.497", "name": "Hòa Vang",    "parent_code": "48", "level": 2, "lng_min": 107.985, "lat_min": 15.945, "lng_max": 108.190, "lat_max": 16.180},
    {"code": "48.498", "name": "Hoàng Sa",    "parent_code": "48", "level": 2, "lng_min": 111.000, "lat_min": 15.700, "lng_max": 113.000, "lat_max": 17.150},
]


def all_admin_rows() -> list[AdminUnitRow]:
    """Return every row in seed-load order (provinces first, then districts)."""
    return list(PROVINCES) + HANOI_DISTRICTS + HCMC_DISTRICTS + DA_NANG_DISTRICTS
