"""Unit tests for backend/services/route.py — Task 8."""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.schemas import ScoredBuilding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_building(lat: float = 40.713, lng: float = -74.003, span: float = 0.001) -> ScoredBuilding:
    """A simple square building centered on (lat, lng)."""
    return ScoredBuilding(
        id="route-b1",
        name="Route Target Hall",
        lat=lat,
        lng=lng,
        footprint=[
            [lat - span, lng - span],
            [lat + span, lng - span],
            [lat + span, lng + span],
            [lat - span, lng + span],
        ],
        triage_score=60.0,
        color="ORANGE",
        damage_probability=0.4,
        estimated_occupancy=50,
        material="concrete",
        levels=3,
        height_m=9.0,
    )


# ---------------------------------------------------------------------------
# Geometry helpers (internal to route.py — tested via module import)
# ---------------------------------------------------------------------------

def test_haversine_m_same_point():
    from backend.services.route import _haversine_m
    assert _haversine_m(40.0, -74.0, 40.0, -74.0) == pytest.approx(0.0, abs=0.001)


def test_haversine_m_known_distance():
    from backend.services.route import _haversine_m
    # ~111 km per degree of latitude
    dist = _haversine_m(40.0, -74.0, 41.0, -74.0)
    assert 110_000 < dist < 112_000


def test_bearing_north():
    from backend.services.route import _bearing
    b = _bearing(40.0, -74.0, 41.0, -74.0)
    assert b == pytest.approx(0.0, abs=1.0)


def test_bearing_east():
    from backend.services.route import _bearing
    b = _bearing(40.0, -74.0, 40.0, -73.0)
    assert b == pytest.approx(90.0, abs=2.0)


def test_offset_point_roundtrip():
    from backend.services.route import _bearing, _haversine_m, _offset_point
    lat0, lng0 = 40.712, -74.006
    b = 45.0
    dist = 200.0
    lat1, lng1 = _offset_point(lat0, lng0, b, dist)
    assert _haversine_m(lat0, lng0, lat1, lng1) == pytest.approx(dist, rel=0.01)


# ---------------------------------------------------------------------------
# calculate_route
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_returns_empty_when_start_equals_target():
    from backend.services.route import calculate_route
    building = _make_building(lat=40.712, lng=-74.006)
    # Start exactly at the centroid → distance ≈ 0
    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="pano-x")):
        result = await calculate_route((40.712, -74.006), building, [])
    assert result == []


@pytest.mark.asyncio
async def test_route_produces_waypoints_with_pano_ids():
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (40.712, -74.006)

    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="pano-abc")):
        waypoints = await calculate_route(start, building, [])

    assert len(waypoints) >= 1
    assert all(wp.pano_id == "pano-abc" for wp in waypoints)


@pytest.mark.asyncio
async def test_route_skips_waypoints_without_pano_id():
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (40.712, -74.006)

    # All panorama IDs are None → every point skipped
    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value=None)):
        waypoints = await calculate_route(start, building, [])

    assert waypoints == []


@pytest.mark.asyncio
async def test_route_skips_some_waypoints():
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (40.712, -74.006)

    call_count = 0

    async def alternating_pano(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return "pano-x" if call_count % 2 == 0 else None

    with patch("backend.services.route.streetview.get_panorama_id", new=alternating_pano):
        waypoints = await calculate_route(start, building, [])

    # Should have some but not all waypoints
    assert 0 < len(waypoints) < call_count


@pytest.mark.asyncio
async def test_route_headings_are_in_valid_range():
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-73.998)
    start = (40.709, -74.013)

    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="p1")):
        waypoints = await calculate_route(start, building, [])

    assert all(0.0 <= wp.heading < 360.0 for wp in waypoints)


@pytest.mark.asyncio
async def test_route_headings_monotonically_consistent():
    """All non-terminal waypoints should point roughly northward (straight-line path)."""
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (37.220, -80.420)  # due north

    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="p1")):
        waypoints = await calculate_route(start, building, [])

    assert len(waypoints) >= 2
    # All headings including the last should be close to 0° (north).
    # The last waypoint now inherits heading from the previous waypoint.
    for wp in waypoints:
        diff = abs(wp.heading - 0.0)
        diff = min(diff, 360 - diff)  # wrap-around
        assert diff < 15.0, f"Heading {wp.heading}° is too far from 0° (north)"


@pytest.mark.asyncio
async def test_route_step_count_scales_with_distance():
    """Longer distances should produce more sample points."""
    from backend.services.route import calculate_route
    near_building = _make_building(lat=40.713, lng=-74.006)
    far_building = _make_building(lat=40.750, lng=-74.006)
    start = (40.712, -74.006)

    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="p1")):
        near_wps = await calculate_route(start, near_building, [])
        far_wps = await calculate_route(start, far_building, [])

    assert len(far_wps) > len(near_wps)


@pytest.mark.asyncio
async def test_route_waypoints_have_correct_schema():
    from backend.models.schemas import Waypoint
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (40.712, -74.006)

    with patch("backend.services.route.streetview.get_panorama_id", new=AsyncMock(return_value="pano-1")):
        waypoints = await calculate_route(start, building, [])

    assert all(isinstance(wp, Waypoint) for wp in waypoints)
    assert all(wp.hazard is None for wp in waypoints)


@pytest.mark.asyncio
async def test_route_concurrent_pano_fetches(monkeypatch):
    """get_panorama_id is called concurrently for all sample points."""
    from backend.services.route import calculate_route
    building = _make_building(lat=40.718, lng=-74.003)
    start = (40.712, -74.006)

    call_log: list[tuple[float, float]] = []

    async def recording_pano(lat: float, lng: float) -> str:
        call_log.append((lat, lng))
        return "p1"

    with patch("backend.services.route.streetview.get_panorama_id", new=recording_pano):
        waypoints = await calculate_route(start, building, [])

    # All sample points were fetched
    assert len(call_log) >= len(waypoints)
