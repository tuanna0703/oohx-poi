"""Grid generator for the tiled-ingestion endpoint."""

from __future__ import annotations

import math

import pytest

from poi_lake.api.v1.admin import _grid_centers


def test_single_cell_when_bbox_smaller_than_cell() -> None:
    # ~1.1km × 1.1km bbox, 5km cells → exactly 1 centre.
    bbox = [105.85, 21.025, 105.86, 21.035]
    centers = _grid_centers(bbox, cell_size_m=5000)
    assert len(centers) == 1
    lat, lng = centers[0]
    assert 21.025 < lat < 21.035
    assert 105.85 < lng < 105.86


def test_grid_covers_hanoi_urban_core() -> None:
    # Inner Hanoi, ~14km wide × 17km tall.
    bbox = [105.78, 20.95, 105.92, 21.10]
    centers = _grid_centers(bbox, cell_size_m=5000)
    assert 6 <= len(centers) <= 18
    # All centres are inside the bbox.
    for lat, lng in centers:
        assert 105.78 < lng < 105.92
        assert 20.95 < lat < 21.10


def test_smaller_cell_size_more_cells() -> None:
    bbox = [105.78, 20.95, 105.92, 21.10]
    big = _grid_centers(bbox, cell_size_m=10000)
    small = _grid_centers(bbox, cell_size_m=2000)
    assert len(small) > len(big)


def test_cells_are_roughly_evenly_spaced() -> None:
    """Adjacent centres are ~cell_size_m apart on the lat axis (rounded
    down so the grid covers the whole bbox; e.g. 11km / 3km → 4 rows of
    ~2.75km each)."""
    centers = _grid_centers([105.0, 21.0, 105.10, 21.10], cell_size_m=3000)
    lats = sorted({c[0] for c in centers})
    if len(lats) >= 2:
        delta_deg = lats[1] - lats[0]
        delta_m = delta_deg * 111_000
        # Step is bbox_size / ceil(bbox_size / cell_size), so always ≤ cell_size.
        assert 1500 <= delta_m <= 3100


def test_invalid_bbox_raises() -> None:
    with pytest.raises(ValueError):
        _grid_centers([105.92, 21.10, 105.78, 20.95], cell_size_m=5000)  # reversed
