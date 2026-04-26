"""One-shot smoke test for the Cau Giay education sweep — used in PR demos."""
import httpx, sys

key = sys.argv[1] if len(sys.argv) > 1 else "pl_r_-seiiu1AbSaPj8dJdg4jdxyjxz0OsMGBcs4A98Ke4"
r = httpx.get(
    "http://localhost:8000/api/v1/master-pois",
    headers={"X-API-Key": key},
    params={
        "lat": 21.0375, "lng": 105.795, "radius_m": 4000,
        "category": "education", "per_page": 30,
    },
)
data = r.json()
print(f"total: {data['total']}")
print()
for it in data["items"][:15]:
    name = it["canonical_name"][:48]
    sub = it["openooh_subcategory"] or "-"
    phone = it["canonical_phone"] or "-"
    print(f"  {name:<48} | {sub:<35} | {phone}")
