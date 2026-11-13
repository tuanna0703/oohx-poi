"""The 2025 ward boundaries' export: what is read from it, and what is refused."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from poi_lake.services import admin_wards
from poi_lake.services.admin_wards import parse_ward, read_archive

SQUARE = {"type": "MultiPolygon", "coordinates": [[[[108.2, 16.0], [108.3, 16.0], [108.3, 16.1], [108.2, 16.0]]]]}


def _fc(props: dict, geom: dict | None = None, fid: str | None = None) -> bytes:
    f = {"type": "Feature", "id": fid or props.get("code"), "properties": props, "geometry": geom or SQUARE}
    return json.dumps({"type": "FeatureCollection", "features": [f]}).encode()


def _archive(monkeypatch: pytest.MonkeyPatch, wards: dict[str, bytes], provinces: int = 2) -> io.BytesIO:
    monkeypatch.setattr(admin_wards, "EXPECTED_PROVINCES", provinces)
    monkeypatch.setattr(admin_wards, "EXPECTED_WARDS", len(wards))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("geojson/48_da_nang/48_da_nang.geojson", _fc({"code": "48", "name": "Đà Nẵng"}))
        z.writestr("geojson/79_ho_chi_minh/79_ho_chi_minh.geojson", _fc({"code": "79", "name": "Hồ Chí Minh"}))
        z.writestr("geojson/README.md", "x")
        for path, raw in wards.items():
            z.writestr(path, raw)
    buf.seek(0)
    return buf


def test_a_ward_file() -> None:
    w = parse_ward(_fc({"code": "20275", "name": "An Hải", "fullName": "Phường An Hải"}), "48", "Đà Nẵng", "20275")
    assert (w.code, w.name, w.full_name, w.province_code, w.province_name) == (
        "20275", "An Hải", "Phường An Hải", "48", "Đà Nẵng")
    assert json.loads(w.geometry) == SQUARE


def test_a_ward_file_that_is_not_one() -> None:
    with pytest.raises(ValueError, match="file says"):
        parse_ward(_fc({"code": "20276", "name": "X"}), "48", "Đà Nẵng", "20275")
    with pytest.raises(ValueError, match="geometry"):
        parse_ward(_fc({"code": "20275", "name": "X"}, {"type": "Point", "coordinates": [108, 16]}), "48", "", "20275")
    with pytest.raises(ValueError, match="no name"):
        parse_ward(_fc({"code": "20275", "name": " "}), "48", "", "20275")


def test_the_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    fp = _archive(monkeypatch, {
        "geojson/48_da_nang/wards/20275_an_hai.geojson": _fc({"code": "20275", "name": "An Hải"}),
        "geojson/79_ho_chi_minh/wards/26734_sai_gon.geojson": _fc({"code": "26734", "name": "Sài Gòn"}),
    })
    got = {(w.code, w.province_code, w.province_name) for w in read_archive(fp)}
    assert got == {("20275", "48", "Đà Nẵng"), ("26734", "79", "Hồ Chí Minh")}, "each under its 2025 province"


def test_an_archive_that_is_not_the_whole_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fp = _archive(monkeypatch, {"geojson/48_da_nang/wards/20275_an_hai.geojson": _fc({"code": "20275", "name": "A"})})
    monkeypatch.setattr(admin_wards, "EXPECTED_WARDS", 3321)
    with pytest.raises(ValueError, match="3321 expected"):
        list(read_archive(fp))
    fp = _archive(monkeypatch, {"geojson/48_da_nang/wards/20275_an_hai.geojson": _fc({"code": "20275", "name": "A"})},
                  provinces=34)
    with pytest.raises(ValueError, match="34 expected"):
        list(read_archive(fp))


def test_the_source_is_pinned() -> None:
    assert admin_wards.SOURCE_COMMIT in admin_wards.SOURCE_URL and len(admin_wards.SOURCE_SHA256) == 64
    assert "ORDER BY p.code" in admin_wards.WARD_AT_SQL, "a boundary point always gets the same ward"
