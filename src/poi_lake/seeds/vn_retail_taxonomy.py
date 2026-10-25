"""Vietnam retail & services extension to the OpenOOH taxonomy.

OpenOOH describes DOOH venues; it has no code for most of what lines a
Vietnamese street — tạp hóa, karaoke, tiệm sửa xe, gara ô tô, tiệm vàng.
bizmap.live is an overview of the Vietnamese retail market, so poi-lake
needs them. Each is a level-2 code under an existing OpenOOH level-1 parent:
consumers that read only ``openooh_category`` (the Data Engine maps the 18
top-level codes to its buckets) keep working unchanged.

One entry per code drives everything else:
  * ``openooh_taxonomy.TAXONOMY`` seeds the rows (migration 0007 inserts them),
  * ``openooh_keywords`` searches gosom with ``search``,
  * ``CategoryMapper.infer_from_name`` matches ``name`` (accent-folded, whole
    words; a leading ``^`` anchors to the start of the name),
  * the crawl plan sweeps them per district (``DISTRICT_SWEEP_CODES``).
"""

from __future__ import annotations

from typing import NamedTuple


class VnCategory(NamedTuple):
    code: str
    name: str
    search: tuple[str, ...]  # gosom keywords, Vietnamese as people type them
    name_terms: tuple[str, ...]  # accent-folded, whole words


VN_CATEGORIES: tuple[VnCategory, ...] = (
    # ---- retail ----
    VnCategory(
        "retail.traditional_grocery", "Traditional Grocery (tạp hóa)",
        ("tạp hóa", "cửa hàng tạp hóa", "đại lý bia nước ngọt"),
        ("tap hoa", "tiem tap hoa", "bach hoa tong hop", "dai ly bia", "dai ly nuoc ngot"),
    ),
    VnCategory(
        "retail.markets", "Wet Markets (chợ)",
        ("chợ", "chợ dân sinh"),
        ("^cho", "cho dan sinh", "cho dau moi"),
    ),
    VnCategory(
        "retail.mother_baby", "Mother & Baby",
        ("cửa hàng mẹ và bé", "con cưng", "bibo mart", "kids plaza"),
        ("me va be", "me & be", "con cung", "bibomart", "bibo mart", "kids plaza", "shop me be"),
    ),
    VnCategory(
        "retail.cosmetics", "Cosmetics & Personal Care",
        ("cửa hàng mỹ phẩm", "hasaki", "guardian", "watsons"),
        ("my pham", "hasaki", "guardian", "watsons", "cocolux"),
    ),
    VnCategory(
        "retail.jewelry", "Gold & Jewelry",
        ("tiệm vàng", "cửa hàng trang sức", "pnj", "doji"),
        ("tiem vang", "vang bac", "kim hoan", "trang suc", "pnj", "doji", "bao tin minh chau"),
    ),
    VnCategory(
        "retail.eyewear", "Eyewear & Opticians",
        ("cửa hàng mắt kính", "kính mắt"),
        ("mat kinh", "kinh mat", "optic", "eyewear", "mat viet"),
    ),
    VnCategory(
        "retail.bookstores", "Bookstores & Stationery",
        ("nhà sách", "văn phòng phẩm"),
        ("nha sach", "van phong pham", "fahasa", "hieu sach"),
    ),
    VnCategory(
        "retail.furniture", "Furniture & Home",
        ("cửa hàng nội thất", "thiết bị vệ sinh"),
        ("noi that", "thiet bi ve sinh", "do go", "chan ga goi"),
    ),
    VnCategory(
        "retail.building_materials", "Building Materials & Hardware",
        ("vật liệu xây dựng", "cửa hàng điện nước", "sắt thép"),
        ("vat lieu xay dung", "vlxd", "dien nuoc", "sat thep", "kim khi", "son nuoc", "gach men"),
    ),
    VnCategory(
        "retail.agri_supplies", "Agricultural Supplies",
        ("vật tư nông nghiệp", "phân bón", "thuốc bảo vệ thực vật"),
        ("vat tu nong nghiep", "phan bon", "thuoc bao ve thuc vat", "thuoc bvtv", "cam gao", "thuc an chan nuoi"),
    ),
    VnCategory(
        "retail.motorbike_dealers", "Motorbike Dealers",
        ("đại lý xe máy", "head honda", "yamaha town", "cửa hàng xe điện"),
        ("dai ly xe may", "cua hang xe may", "head honda", "honda head", "yamaha town", "xe dien"),
    ),
    VnCategory(
        "retail.auto_repair", "Car Repair & Garages",
        ("gara ô tô", "sửa chữa ô tô", "garage"),
        ("gara", "garage", "sua chua o to", "sua o to", "sua xe o to", "sua chua oto", "lop o to"),
    ),
    VnCategory(
        "retail.motorbike_repair", "Motorbike Repair",
        ("sửa xe máy", "tiệm sửa xe"),
        ("sua xe may", "sua chua xe may", "tiem sua xe", "sua xe", "va vo xe", "thay nhot"),
    ),
    VnCategory(
        "retail.car_wash", "Car & Motorbike Wash",
        ("rửa xe", "chăm sóc xe"),
        ("rua xe", "cham soc xe", "detailing"),
    ),
    VnCategory(
        "retail.pet_stores", "Pet Shops",
        ("pet shop", "cửa hàng thú cưng"),
        ("pet shop", "petshop", "thu cung", "pet mart", "petmart"),
    ),
    VnCategory(
        "retail.florists", "Florists",
        ("shop hoa", "cửa hàng hoa tươi"),
        ("shop hoa", "hoa tuoi", "tiem hoa"),
    ),
    # ---- hospitality ----
    VnCategory(
        "hospitality.milk_tea", "Milk Tea & Desserts",
        ("trà sữa", "quán chè"),
        ("tra sua", "milk tea", "tra chanh", "^che", "quan che"),
    ),
    VnCategory(
        "hospitality.beer_halls", "Beer Halls (quán nhậu, bia hơi)",
        ("quán nhậu", "bia hơi"),
        ("quan nhau", "bia hoi", "bia tuoi"),
    ),
    # ---- health & beauty ----
    VnCategory(
        "health_and_beauty.nail_salons", "Nail Salons",
        ("tiệm nail",),
        ("nail", "nails", "lam mong"),
    ),
    # ---- entertainment ----
    VnCategory(
        "entertainment.karaoke", "Karaoke",
        ("karaoke",),
        ("karaoke", "kara"),
    ),
    VnCategory(
        "entertainment.billiards", "Billiards",
        ("bida", "câu lạc bộ bida"),
        ("bida", "billiards", "billiard", "bi-a"),
    ),
    VnCategory(
        "entertainment.gaming", "Internet & Game Centers",
        ("quán net", "phòng game"),
        ("quan net", "phong net", "phong game", "game center", "cyber game", "cybergame"),
    ),
    # ---- financial ----
    VnCategory(
        "financial.pawn_shops", "Pawn Shops",
        ("tiệm cầm đồ", "f88"),
        ("cam do", "f88", "cam vay"),
    ),
    # ---- education ----
    VnCategory(
        "education.training_centers", "Language & Training Centers",
        ("trung tâm ngoại ngữ", "trung tâm tiếng anh"),
        ("ngoai ngu", "tieng anh", "anh ngu", "luyen thi", "trung tam dao tao"),
    ),
    # ---- sports ----
    VnCategory(
        "sports_and_fitness.sports_courts", "Sports Courts & Pitches",
        ("sân bóng đá mini", "sân cầu lông", "sân pickleball", "sân tennis"),
        ("san bong", "san cau long", "pickleball", "san tennis", "san quan vot", "bong da mini"),
    ),
)

# What bizmap and the user asked for first (2026-10-25): crawled right after
# the brand sweeps, before the rest of tier 1.
FIRST: tuple[str, ...] = (
    "retail.traditional_grocery",
    "retail.motorbike_repair",
    "retail.auto_repair",
    "entertainment.karaoke",
)

VN_CODES: frozenset[str] = frozenset(c.code for c in VN_CATEGORIES)
