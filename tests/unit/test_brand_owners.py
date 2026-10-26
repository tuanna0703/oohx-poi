from poi_lake.catalog.brand_owners import owner_of, slugify


def test_known_brand_has_its_company() -> None:
    o = owner_of("p/s")
    assert o is not None and (o.name, o.slug, o.company, o.company_slug) == ("P/S", "p-s", "Unilever", "unilever")


def test_private_label_and_unknown_and_none() -> None:
    assert owner_of("WMHOME").private_label  # type: ignore[union-attr]
    unknown = owner_of("THIÊN NHIÊN XANH")
    assert unknown is not None and unknown.company is None and unknown.slug == "thien-nhien-xanh"
    assert owner_of("NOBRAND") is None and owner_of(None) is None


def test_slugify() -> None:
    assert slugify("TEA+") == "tea-plus"
    assert slugify("Đà Lạt Milk") == "da-lat-milk"
