"""Tests for backend/services/streetview.py — image fetch, pano ID, viewpoint geometry."""
from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Geometry helper unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bearing,expected", [
    (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
    (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
])
def test_bearing_to_cardinal(bearing, expected):
    from backend.services.streetview import _bearing_to_cardinal
    assert _bearing_to_cardinal(bearing) == expected


def test_bearing_to_cardinal_north_wraps():
    from backend.services.streetview import _bearing_to_cardinal
    assert _bearing_to_cardinal(360) == "N"


def test_offset_point_moves_north():
    from backend.services.streetview import _offset_point
    new_lat, new_lng = _offset_point(40.0, -74.0, 0.0, 1000.0)
    assert new_lat > 40.0
    assert abs(new_lng - (-80.0)) < 1e-4


def test_offset_point_moves_east():
    from backend.services.streetview import _offset_point
    new_lat, new_lng = _offset_point(40.0, -74.0, 90.0, 1000.0)
    assert abs(new_lat - 40.0) < 1e-3
    assert new_lng > -80.0


def test_bearing_north_to_south():
    from backend.services.streetview import _bearing
    result = _bearing(0.0, 0.0, -1.0, 0.0)
    assert result == pytest.approx(180.0, abs=0.5)


def test_bearing_south_to_north():
    from backend.services.streetview import _bearing
    result = _bearing(-1.0, 0.0, 0.0, 0.0)
    assert result == pytest.approx(0.0, abs=0.5)


def test_image_cache_key_format():
    from backend.services.streetview import _image_cache_key
    key = _image_cache_key(40.7123, -74.0060, 180.0)
    assert key == "40.712300_-74.006000_180.0"


# ---------------------------------------------------------------------------
# calculate_viewpoints
# ---------------------------------------------------------------------------

def test_calculate_viewpoints_small_building_returns_4(minimal_footprint):
    from backend.services.streetview import calculate_viewpoints
    vps = calculate_viewpoints(minimal_footprint, 40.71, -74.01)
    assert len(vps) == 4


def test_calculate_viewpoints_large_building_returns_8(large_footprint):
    from backend.services.streetview import calculate_viewpoints
    vps = calculate_viewpoints(large_footprint, 40.70, -74.01)
    assert len(vps) == 8


def test_calculate_viewpoints_epicenter_facing_is_first(minimal_footprint):
    from backend.services.streetview import calculate_viewpoints
    # Centroid ~37.22835 — epicenter due north at 37.30 → first viewpoint faces N
    vps = calculate_viewpoints(minimal_footprint, epicenter_lat=41.00, epicenter_lng=-74.0060)
    assert vps[0].facing == "N"


def test_calculate_viewpoints_headings_face_building(minimal_footprint):
    from backend.services.streetview import _bearing, _centroid, calculate_viewpoints
    vps = calculate_viewpoints(minimal_footprint, 40.71, -74.01)
    c_lat, c_lng = _centroid(minimal_footprint)
    for vp in vps:
        expected_heading = _bearing(vp.lat, vp.lng, c_lat, c_lng)
        assert abs(vp.heading - expected_heading) < 5.0


def test_calculate_viewpoints_empty_footprint():
    from backend.services.streetview import calculate_viewpoints
    assert calculate_viewpoints([], 40.71, -74.01) == []


def test_calculate_viewpoints_standoff_moves_point(minimal_footprint):
    from backend.services.streetview import _centroid, calculate_viewpoints
    vps = calculate_viewpoints(minimal_footprint, 40.71, -74.01, standoff_m=50.0)
    c_lat, c_lng = _centroid(minimal_footprint)
    R = 6_371_000.0
    for vp in vps:
        dlat = math.radians(vp.lat - c_lat)
        dlng = math.radians(vp.lng - c_lng)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(c_lat)) * math.cos(math.radians(vp.lat)) * math.sin(dlng / 2) ** 2
        dist = R * 2 * math.asin(math.sqrt(a))
        assert abs(dist - 50.0) < 5.0, f"Viewpoint {dist:.1f}m from centroid, expected ~50m"


# ---------------------------------------------------------------------------
# populate_pano_cache
# ---------------------------------------------------------------------------

def test_populate_pano_cache():
    import backend.services.streetview as sv_mod
    from backend.services.streetview import populate_pano_cache
    populate_pano_cache(40.7136, -74.0066, "test-pano-id")
    assert sv_mod._PANO_CACHE[(40.7136, -74.0066)] == "test-pano-id"


# ---------------------------------------------------------------------------
# get_panorama_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_panorama_id_hits_in_memory_cache_first():
    import backend.services.streetview as sv_mod
    from backend.services.streetview import get_panorama_id
    sv_mod._PANO_CACHE[(40.7136, -74.0066)] = "cached-id"
    result = await get_panorama_id(40.7136, -74.0066)
    assert result == "cached-id"


@pytest.mark.asyncio
async def test_get_panorama_id_falls_through_to_disk_index():
    import backend.services.streetview as sv_mod
    from backend.services.streetview import get_panorama_id
    with patch("backend.services.streetview._load_pano_index", return_value={"40.7136,-74.0066": "disk-pano"}):
        result = await get_panorama_id(40.7136, -74.0066)
    assert result == "disk-pano"
    assert sv_mod._PANO_CACHE[(40.7136, -74.0066)] == "disk-pano"


@pytest.mark.asyncio
async def test_get_panorama_id_demo_mode_returns_none_on_miss(demo_settings):
    from backend.services.streetview import get_panorama_id
    with patch("backend.services.streetview._load_pano_index", return_value={}):
        result = await get_panorama_id(99.0, 99.0)
    assert result is None


@pytest.mark.asyncio
async def test_get_panorama_id_live_mode_calls_api(live_settings):
    import backend.services.streetview as sv_mod
    from backend.services.streetview import get_panorama_id

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "OK", "pano_id": "live-pano"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.streetview._load_pano_index", return_value={}), \
         patch("backend.services.streetview.httpx.AsyncClient", return_value=mock_cm):
        result = await get_panorama_id(40.7136, -74.0066)

    assert result == "live-pano"
    assert sv_mod._PANO_CACHE[(40.7136, -74.0066)] == "live-pano"


@pytest.mark.asyncio
async def test_get_panorama_id_api_status_not_ok(live_settings):
    from backend.services.streetview import get_panorama_id

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "ZERO_RESULTS"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.streetview._load_pano_index", return_value={}), \
         patch("backend.services.streetview.httpx.AsyncClient", return_value=mock_cm):
        result = await get_panorama_id(40.712, -74.006)

    assert result is None


# ---------------------------------------------------------------------------
# fetch_street_view_image
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_street_view_image_live_calls_api(live_settings):
    import backend.services.streetview as sv_mod
    from backend.services.streetview import fetch_street_view_image

    mock_resp = MagicMock()
    mock_resp.content = b"\xff\xd8\xff\xe0JPEG"
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.streetview.httpx.AsyncClient", return_value=mock_cm):
        result = await fetch_street_view_image(40.712, -74.006, 90.0)

    assert result == b"\xff\xd8\xff\xe0JPEG"
    assert sv_mod._call_count == 1


@pytest.mark.asyncio
async def test_fetch_street_view_image_demo_mode_cache_hit(demo_settings, fake_image_bytes):
    from backend.services.streetview import fetch_street_view_image

    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_bytes.return_value = fake_image_bytes

    with patch("backend.services.streetview._IMAGE_DIR", MagicMock(__truediv__=MagicMock(return_value=mock_path))), \
         patch("backend.services.streetview.httpx.AsyncClient") as mock_client_cls:
        result = await fetch_street_view_image(40.712, -74.006, 90.0)

    assert result == fake_image_bytes
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_street_view_image_demo_mode_cache_miss_falls_back_to_api(demo_settings):
    from backend.services.streetview import fetch_street_view_image

    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False

    mock_resp = MagicMock()
    mock_resp.content = b"\xff\xd8API"
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.streetview._IMAGE_DIR", MagicMock(__truediv__=MagicMock(return_value=mock_path))), \
         patch("backend.services.streetview.httpx.AsyncClient", return_value=mock_cm):
        result = await fetch_street_view_image(40.712, -74.006, 90.0)

    assert result == b"\xff\xd8API"


@pytest.mark.asyncio
async def test_call_count_increments_on_live_fetch(live_settings):
    import backend.services.streetview as sv_mod
    from backend.services.streetview import fetch_street_view_image

    mock_resp = MagicMock()
    mock_resp.content = b"\xff\xd8"
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.services.streetview.httpx.AsyncClient", return_value=mock_cm):
        await fetch_street_view_image(40.712, -74.006, 90.0)
        await fetch_street_view_image(40.712, -74.006, 180.0)

    assert sv_mod._call_count == 2


def test_quota_warn_at_threshold(caplog):
    import backend.services.streetview as sv_mod
    from backend.services.streetview import _increment_call_count
    sv_mod._call_count = 499
    import logging
    with caplog.at_level(logging.WARNING, logger="backend.services.streetview"):
        _increment_call_count()
    assert sv_mod._call_count == 500
    assert any("500" in r.message or "quota" in r.message.lower() for r in caplog.records)
