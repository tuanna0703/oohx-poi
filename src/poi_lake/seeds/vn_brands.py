"""Vietnam brand dictionary.

Covers the major VN chains across convenience, grocery, F&B, retail, pharmacy,
banking, fuel, cinema, malls, and logistics. This is the initial migration of
the 62-brand pattern list from the legacy POI crawler; new brands should be
added via follow-up migrations (not edits here) so history is preserved.

Each entry has:
  - ``name``: canonical brand name
  - ``aliases``: other spellings we might see in raw data (lowercased at match time)
  - ``category``: OpenOOH top-level category code
  - ``parent_company``: for rollups
  - ``match_pattern``: Python regex (case-insensitive); empty string == alias-only matching
"""

from __future__ import annotations

from typing import TypedDict


class BrandRow(TypedDict):
    name: str
    aliases: list[str]
    category: str
    parent_company: str | None
    country: str
    match_pattern: str


BRANDS: list[BrandRow] = [
    # -------- Convenience stores --------
    {"name": "Circle K", "aliases": ["CircleK", "circlek", "circle-k"], "category": "retail", "parent_company": "Alimentation Couche-Tard", "country": "VN", "match_pattern": r"\bcircle\s*[-]?k\b"},
    {"name": "GS25", "aliases": ["GS 25", "gs25"], "category": "retail", "parent_company": "GS Retail", "country": "VN", "match_pattern": r"\bgs\s*25\b"},
    {"name": "FamilyMart", "aliases": ["Family Mart", "familymart"], "category": "retail", "parent_company": "FamilyMart", "country": "VN", "match_pattern": r"\bfamily\s*mart\b"},
    {"name": "7-Eleven", "aliases": ["7 Eleven", "7eleven", "seven eleven"], "category": "retail", "parent_company": "Seven & i", "country": "VN", "match_pattern": r"\b7[-\s]?eleven\b|\bseven\s*eleven\b"},
    {"name": "Ministop", "aliases": ["Mini Stop", "mini-stop"], "category": "retail", "parent_company": "AEON", "country": "VN", "match_pattern": r"\bmini[-\s]?stop\b"},
    {"name": "WinMart+", "aliases": ["Winmart+", "Vinmart+", "WinMart Plus", "VinMart Plus"], "category": "retail", "parent_company": "Masan Group", "country": "VN", "match_pattern": r"\b(win|vin)mart\s*\+?\b"},
    {"name": "Satrafoods", "aliases": ["Satra Foods", "satra"], "category": "retail", "parent_company": "SATRA", "country": "VN", "match_pattern": r"\bsatra\s*foods?\b"},
    {"name": "Co.op Food", "aliases": ["Coop Food", "Co-op Food"], "category": "retail", "parent_company": "Saigon Co.op", "country": "VN", "match_pattern": r"\bco[\.\-\s]?op\s*food\b"},
    {"name": "Bach Hoa Xanh", "aliases": ["Bách Hóa Xanh", "BHX", "bachhoaxanh"], "category": "retail", "parent_company": "Mobile World", "country": "VN", "match_pattern": r"\bb[aá]ch\s*h[oó]a\s*xanh\b|\bbhx\b"},

    # -------- Supermarkets / hypermarkets / malls --------
    {"name": "Co.opmart", "aliases": ["Coopmart", "Co-op Mart", "Saigon Co.op"], "category": "retail", "parent_company": "Saigon Co.op", "country": "VN", "match_pattern": r"\bco[\.\-\s]?op\s*mart\b"},
    {"name": "Big C", "aliases": ["BigC", "GO!", "Big C / GO!"], "category": "retail", "parent_company": "Central Retail", "country": "VN", "match_pattern": r"\bbig\s*c\b|\bgo\s*!\s*"},
    {"name": "Lotte Mart", "aliases": ["LotteMart", "Lotte-Mart"], "category": "retail", "parent_company": "Lotte Group", "country": "VN", "match_pattern": r"\blotte\s*mart\b"},
    {"name": "AEON", "aliases": ["Aeon Mall", "AeonMall", "Aeon Vietnam"], "category": "retail", "parent_company": "AEON", "country": "VN", "match_pattern": r"\baeon\b"},
    {"name": "MM Mega Market", "aliases": ["MM Market", "Mega Market", "Metro Cash & Carry"], "category": "retail", "parent_company": "TCC Group", "country": "VN", "match_pattern": r"\bmm\s*mega\s*market\b|\bmega\s*market\b"},
    {"name": "WinMart", "aliases": ["Winmart", "Vinmart", "VinMart"], "category": "retail", "parent_company": "Masan Group", "country": "VN", "match_pattern": r"\b(win|vin)mart\b(?!\s*\+)"},
    {"name": "Emart", "aliases": ["E-mart", "E Mart"], "category": "retail", "parent_company": "THACO", "country": "VN", "match_pattern": r"\be[-\s]?mart\b"},
    {"name": "Tops Market", "aliases": ["Tops"], "category": "retail", "parent_company": "Central Retail", "country": "VN", "match_pattern": r"\btops\s*market\b"},
    {"name": "Vincom", "aliases": ["Vincom Center", "Vincom Plaza", "Vincom Mega Mall"], "category": "retail", "parent_company": "Vingroup", "country": "VN", "match_pattern": r"\bvincom\b"},
    {"name": "Crescent Mall", "aliases": ["Crescent"], "category": "retail", "parent_company": "Crescent Mall", "country": "VN", "match_pattern": r"\bcrescent\s*mall\b"},
    {"name": "Lotte Mall", "aliases": ["Lotte Mall West Lake", "Lotte Mall Hanoi"], "category": "retail", "parent_company": "Lotte Group", "country": "VN", "match_pattern": r"\blotte\s*mall\b"},

    # -------- Coffee / F&B chains --------
    {"name": "Highlands Coffee", "aliases": ["Highlands", "HLC"], "category": "hospitality", "parent_company": "Jollibee Foods", "country": "VN", "match_pattern": r"\bhighlands\s*coffee\b|\bhighlands\b(?!\s*park)"},
    {"name": "Starbucks", "aliases": ["Starbucks Coffee"], "category": "hospitality", "parent_company": "Starbucks", "country": "VN", "match_pattern": r"\bstarbucks\b"},
    {"name": "The Coffee House", "aliases": ["Coffee House", "TCH"], "category": "hospitality", "parent_company": "Seedcom", "country": "VN", "match_pattern": r"\bthe\s*coffee\s*house\b|\bcoffee\s*house\b"},
    {"name": "Trung Nguyen Legend", "aliases": ["Trung Nguyên", "Trung Nguyen", "TN Legend"], "category": "hospitality", "parent_company": "Trung Nguyen", "country": "VN", "match_pattern": r"\btrung\s*nguy[eê]n\b"},
    {"name": "Phuc Long", "aliases": ["Phúc Long"], "category": "hospitality", "parent_company": "Masan Group", "country": "VN", "match_pattern": r"\bph[uú]c\s*long\b"},
    {"name": "Cong Caphe", "aliases": ["Cộng Cà Phê", "Cong Ca Phe"], "category": "hospitality", "parent_company": "Cong Caphe", "country": "VN", "match_pattern": r"\bc[oộ]ng\s*c[àa]\s*ph[êe]\b"},
    {"name": "Katinat", "aliases": ["Katinat Saigon Kafe"], "category": "hospitality", "parent_company": "D1 Concepts", "country": "VN", "match_pattern": r"\bkatinat\b"},
    {"name": "Phe La", "aliases": ["Phê La"], "category": "hospitality", "parent_company": "Phe La", "country": "VN", "match_pattern": r"\bph[êe]\s*la\b"},

    # -------- Fast food --------
    {"name": "KFC", "aliases": ["Kentucky Fried Chicken"], "category": "hospitality", "parent_company": "Yum! Brands", "country": "VN", "match_pattern": r"\bkfc\b|\bkentucky\s*fried\s*chicken\b"},
    {"name": "McDonald's", "aliases": ["McDonalds", "Mc Donald's", "McD"], "category": "hospitality", "parent_company": "McDonald's", "country": "VN", "match_pattern": r"\bmc\s*donald'?s?\b"},
    {"name": "Lotteria", "aliases": ["Lotte Eatz"], "category": "hospitality", "parent_company": "Lotte Group", "country": "VN", "match_pattern": r"\blotteria\b"},
    {"name": "Jollibee", "aliases": [], "category": "hospitality", "parent_company": "Jollibee Foods", "country": "VN", "match_pattern": r"\bjollibee\b"},
    {"name": "Pizza Hut", "aliases": [], "category": "hospitality", "parent_company": "Yum! Brands", "country": "VN", "match_pattern": r"\bpizza\s*hut\b"},
    {"name": "Domino's Pizza", "aliases": ["Dominos", "Domino Pizza"], "category": "hospitality", "parent_company": "Domino's", "country": "VN", "match_pattern": r"\bdomino'?s\b"},
    {"name": "The Pizza Company", "aliases": ["Pizza Company"], "category": "hospitality", "parent_company": "Minor Food", "country": "VN", "match_pattern": r"\bthe\s*pizza\s*company\b|\bpizza\s*company\b"},
    {"name": "Burger King", "aliases": [], "category": "hospitality", "parent_company": "Restaurant Brands Intl.", "country": "VN", "match_pattern": r"\bburger\s*king\b"},
    {"name": "Texas Chicken", "aliases": ["Church's Texas Chicken"], "category": "hospitality", "parent_company": "Inspire Brands", "country": "VN", "match_pattern": r"\btexas\s*chicken\b"},
    {"name": "Pho 24", "aliases": ["Phở 24"], "category": "hospitality", "parent_company": "Jollibee Foods", "country": "VN", "match_pattern": r"\bph[ởo]\s*24\b"},

    # -------- Consumer electronics --------
    {"name": "Thegioididong", "aliases": ["Thế Giới Di Động", "The Gioi Di Dong", "TGDĐ"], "category": "retail", "parent_company": "Mobile World", "country": "VN", "match_pattern": r"\bth[eế]\s*gi[oớ]i\s*di\s*đ[oộ]ng\b|\btgd[đd]\b|\bthegioididong\b"},
    {"name": "FPT Shop", "aliases": ["FPTShop"], "category": "retail", "parent_company": "FPT Retail", "country": "VN", "match_pattern": r"\bfpt\s*shop\b"},
    {"name": "Dien May Xanh", "aliases": ["Điện Máy Xanh", "DMX"], "category": "retail", "parent_company": "Mobile World", "country": "VN", "match_pattern": r"\bđi[eệ]n\s*m[aá]y\s*xanh\b|\bdmx\b"},
    {"name": "Nguyen Kim", "aliases": ["Nguyễn Kim"], "category": "retail", "parent_company": "Central Retail", "country": "VN", "match_pattern": r"\bnguy[eễ]n\s*kim\b"},
    {"name": "CellphoneS", "aliases": ["Cellphone S", "cellphones"], "category": "retail", "parent_company": "CellphoneS", "country": "VN", "match_pattern": r"\bcellphones\b"},
    {"name": "Phong Vu", "aliases": ["Phong Vũ"], "category": "retail", "parent_company": "Phong Vu", "country": "VN", "match_pattern": r"\bphong\s*v[uũ]\b"},

    # -------- Pharmacy / Health & Beauty --------
    {"name": "Pharmacity", "aliases": [], "category": "retail", "parent_company": "Pharmacity", "country": "VN", "match_pattern": r"\bpharmacity\b"},
    {"name": "Long Chau", "aliases": ["Long Châu", "Nhà thuốc Long Châu"], "category": "retail", "parent_company": "FPT Retail", "country": "VN", "match_pattern": r"\blong\s*ch[aâ]u\b"},
    {"name": "An Khang", "aliases": ["Nhà thuốc An Khang"], "category": "retail", "parent_company": "Mobile World", "country": "VN", "match_pattern": r"\ban\s*khang\b"},
    {"name": "Guardian", "aliases": [], "category": "health_and_beauty", "parent_company": "DFI Retail Group", "country": "VN", "match_pattern": r"\bguardian\b"},
    {"name": "Watsons", "aliases": [], "category": "health_and_beauty", "parent_company": "A.S. Watson", "country": "VN", "match_pattern": r"\bwatsons\b"},
    {"name": "Medicare", "aliases": [], "category": "health_and_beauty", "parent_company": "Medicare", "country": "VN", "match_pattern": r"\bmedicare\b"},
    {"name": "Hasaki", "aliases": ["Hasaki Beauty"], "category": "health_and_beauty", "parent_company": "Hasaki", "country": "VN", "match_pattern": r"\bhasaki\b"},
    {"name": "Sociolla", "aliases": [], "category": "health_and_beauty", "parent_company": "Social Bella", "country": "VN", "match_pattern": r"\bsociolla\b"},

    # -------- Banking (financial) --------
    {"name": "Vietcombank", "aliases": ["VCB", "Ngân hàng TMCP Ngoại thương Việt Nam"], "category": "financial", "parent_company": "Vietcombank", "country": "VN", "match_pattern": r"\bvietcombank\b|\bvcb\b"},
    {"name": "VietinBank", "aliases": ["CTG", "Vietin Bank"], "category": "financial", "parent_company": "VietinBank", "country": "VN", "match_pattern": r"\bvietin\s*bank\b|\bvietinbank\b"},
    {"name": "BIDV", "aliases": ["Ngân hàng Đầu tư và Phát triển Việt Nam"], "category": "financial", "parent_company": "BIDV", "country": "VN", "match_pattern": r"\bbidv\b"},
    {"name": "Agribank", "aliases": ["Ngân hàng Nông nghiệp"], "category": "financial", "parent_company": "Agribank", "country": "VN", "match_pattern": r"\bagribank\b"},
    {"name": "Techcombank", "aliases": ["TCB", "Techcom Bank"], "category": "financial", "parent_company": "Techcombank", "country": "VN", "match_pattern": r"\btechcom\s*bank\b|\btechcombank\b"},
    {"name": "Sacombank", "aliases": ["STB"], "category": "financial", "parent_company": "Sacombank", "country": "VN", "match_pattern": r"\bsacombank\b"},
    {"name": "ACB", "aliases": ["Asia Commercial Bank"], "category": "financial", "parent_company": "ACB", "country": "VN", "match_pattern": r"\bacb\b"},
    {"name": "MB Bank", "aliases": ["MBBank", "Military Bank"], "category": "financial", "parent_company": "MB Bank", "country": "VN", "match_pattern": r"\bmb\s*bank\b|\bmbbank\b"},
    {"name": "VPBank", "aliases": ["VP Bank"], "category": "financial", "parent_company": "VPBank", "country": "VN", "match_pattern": r"\bvp\s*bank\b|\bvpbank\b"},
    {"name": "HDBank", "aliases": ["HD Bank"], "category": "financial", "parent_company": "HDBank", "country": "VN", "match_pattern": r"\bhd\s*bank\b|\bhdbank\b"},
    {"name": "TPBank", "aliases": ["Tien Phong Bank", "TP Bank"], "category": "financial", "parent_company": "TPBank", "country": "VN", "match_pattern": r"\btp\s*bank\b|\btpbank\b"},
    {"name": "SHB", "aliases": ["Saigon-Hanoi Bank"], "category": "financial", "parent_company": "SHB", "country": "VN", "match_pattern": r"\bshb\b"},

    # -------- Gas stations --------
    {"name": "Petrolimex", "aliases": ["PLX"], "category": "retail", "parent_company": "Petrolimex", "country": "VN", "match_pattern": r"\bpetrolimex\b"},
    {"name": "PVOil", "aliases": ["PV Oil", "PetroVietnam Oil"], "category": "retail", "parent_company": "PetroVietnam", "country": "VN", "match_pattern": r"\bpv\s*oil\b|\bpvoil\b"},
    {"name": "Saigon Petro", "aliases": ["SaigonPetro"], "category": "retail", "parent_company": "Saigon Petro", "country": "VN", "match_pattern": r"\bsaigon\s*petro\b"},
    {"name": "Shell", "aliases": [], "category": "retail", "parent_company": "Shell", "country": "VN", "match_pattern": r"\bshell\b"},
    {"name": "Caltex", "aliases": ["Chevron"], "category": "retail", "parent_company": "Chevron", "country": "VN", "match_pattern": r"\bcaltex\b"},

    # -------- Cinema --------
    {"name": "CGV", "aliases": ["CGV Cinemas"], "category": "entertainment", "parent_company": "CJ CGV", "country": "VN", "match_pattern": r"\bcgv\b"},
    {"name": "Lotte Cinema", "aliases": [], "category": "entertainment", "parent_company": "Lotte Group", "country": "VN", "match_pattern": r"\blotte\s*cinema\b"},
    {"name": "Galaxy Cinema", "aliases": ["Galaxy"], "category": "entertainment", "parent_company": "Galaxy Studio", "country": "VN", "match_pattern": r"\bgalaxy\s*cinema\b"},
    {"name": "BHD Star", "aliases": ["BHD", "BHD Star Cineplex"], "category": "entertainment", "parent_company": "BHD", "country": "VN", "match_pattern": r"\bbhd\s*star\b"},

    # -------- Logistics / Post --------
    {"name": "Viettel Post", "aliases": ["ViettelPost", "VTP"], "category": "retail", "parent_company": "Viettel Group", "country": "VN", "match_pattern": r"\bviettel\s*post\b"},
    {"name": "Vietnam Post", "aliases": ["VN Post", "VNPost"], "category": "retail", "parent_company": "Vietnam Post", "country": "VN", "match_pattern": r"\bvietnam\s*post\b|\bvnpost\b"},
    {"name": "Giao Hang Nhanh", "aliases": ["GHN", "Giao Hàng Nhanh"], "category": "retail", "parent_company": "GHN Express", "country": "VN", "match_pattern": r"\bghn\b|\bgiao\s*h[aà]ng\s*nhanh\b"},
    {"name": "Giao Hang Tiet Kiem", "aliases": ["GHTK", "Giao Hàng Tiết Kiệm"], "category": "retail", "parent_company": "GHTK", "country": "VN", "match_pattern": r"\bghtk\b|\bgiao\s*h[aà]ng\s*ti[eế]t\s*ki[eệ]m\b"},
    {"name": "J&T Express", "aliases": ["JT Express"], "category": "retail", "parent_company": "J&T Express", "country": "VN", "match_pattern": r"\bj\s*&?\s*t\s*express\b"},
]
