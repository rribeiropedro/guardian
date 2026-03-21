"""
Task 3: Street View Service
Wraps Google Street View Static API to fetch images and panorama IDs,
and computes viewpoints around a building polygon.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..config import get_settings
from ..models.schemas import ScoutViewpoint

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Path to the directory where precache.py stores images and pano metadata
_CACHE_DIR = Path(__file__).parent.parent / "cache"
_PANO_INDEX_FILE = _CACHE_DIR / "panos.json"
_IMAGE_DIR = _CACHE_DIR / "images"


def _image_cache_key(lat: float, lng: float, heading: float) -> str:
    return f"{lat:.6f}_{lng:.6f}_{heading:.1f}"


def _load_pano_index() -> dict[str, str]:
    """Load the pano index from disk (populated by precache.py)."""
    if _PANO_INDEX_FILE.exists():
        try:
            return json.loads(_PANO_INDEX_FILE.read_text())
        except Exception:
            logger.warning("Failed to read pano index from %s", _PANO_INDEX_FILE)
    return {}

# ---------------------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------------------

_call_count: int = 0
_QUOTA_WARN_THRESHOLD = 500

STREET_VIEW_BASE = "https://maps.googleapis.com/maps/api/streetview"
STREET_VIEW_META = "https://maps.googleapis.com/maps/api/streetview/metadata"

# ---------------------------------------------------------------------------
# Pre-cache: (lat_rounded, lng_rounded) -> panorama_id
# Pre-populated for VT campus demo buildings (Blacksburg, VA ~37.22, -80.42)
# ---------------------------------------------------------------------------
_PANO_CACHE: dict[tuple[float, float], str] = {
    # Burruss Hall
    (37.2296, -80.4236): "CAoSLEFGMVFpcE1uTzNvd1RlVDZtdnE4M2FPeXJfZnFzVTdER0hFZEtJbEJDMlJT",
    # Torgersen Hall
    (37.2274, -80.4240): "CAoSLEFGMVFpcFB6cUFlaXNEeGZkdWtpS2ZZZG5iVHZYUHVpbnJKbEFUSFZ3NXJK",
    # Newman Library
    (37.2282, -80.4228): "CAoSLEFGMVFpcE5sVG43R3BCb1laRExUQXdUSFZJSnZPYlJheU5neEVxTHMwZWVF",
}


def _round_coord(v: float, decimals: int = 4) -> float:
    return round(v, decimals)


def _increment_call_count() -> None:
    global _call_count
    _call_count += 1
    if _call_count == _QUOTA_WARN_THRESHOLD:
        logger.warning(
            "Street View API call count reached %d — approaching quota limit.",
            _QUOTA_WARN_THRESHOLD,
        )


# ---------------------------------------------------------------------------
# Core API helpers
# ---------------------------------------------------------------------------


async def fetch_street_view_image(
    lat: float,
    lng: float,
    heading: float,
    pitch: float = 0,
    fov: int = 90,
    size: str = "640x640",
) -> bytes:
    """Fetch a Street View Static image as raw JPEG bytes.

    In DEMO_MODE, returns the pre-cached image from disk instead of calling the API.
    """
    if get_settings().demo_mode:
        key = _image_cache_key(lat, lng, heading)
        cached_path = _IMAGE_DIR / f"{key}.jpg"
        if cached_path.exists():
            logger.debug("DEMO_MODE: serving cached image %s", cached_path.name)
            return cached_path.read_bytes()
        logger.warning("DEMO_MODE: no cached image for %s — falling back to API", key)

    api_key = get_settings().google_maps_api_key
    params = {
        "location": f"{lat},{lng}",
        "heading": str(heading),
        "pitch": str(pitch),
        "fov": str(fov),
        "size": size,
        "key": api_key,
    }
    _increment_call_count()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(STREET_VIEW_BASE, params=params)
        response.raise_for_status()
        return response.content


async def get_panorama_id(lat: float, lng: float) -> str | None:
    """
    Look up the nearest Street View panorama ID for a given coordinate.
    Returns None if no panorama is available at that location.
    Checks the in-memory cache, then the disk pano index, then the API.
    In DEMO_MODE only the caches are used (no live API call).
    """
    key = (_round_coord(lat), _round_coord(lng))
    if key in _PANO_CACHE:
        return _PANO_CACHE[key]

    # Check disk index (populated by precache.py)
    disk_key = f"{key[0]},{key[1]}"
    disk_index = _load_pano_index()
    if disk_key in disk_index:
        pano_id = disk_index[disk_key]
        _PANO_CACHE[key] = pano_id
        return pano_id

    if get_settings().demo_mode:
        logger.warning("DEMO_MODE: no cached pano for (%.4f, %.4f)", lat, lng)
        return None

    api_key = get_settings().google_maps_api_key
    params = {
        "location": f"{lat},{lng}",
        "key": api_key,
    }
    _increment_call_count()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(STREET_VIEW_META, params=params)
        response.raise_for_status()
        data = response.json()

    if data.get("status") != "OK":
        logger.debug("No panorama at (%.4f, %.4f): status=%s", lat, lng, data.get("status"))
        return None

    pano_id: str = data["pano_id"]
    _PANO_CACHE[key] = pano_id
    return pano_id


def populate_pano_cache(lat: float, lng: float, pano_id: str) -> None:
    """Pre-populate the panorama cache (used by precache.py)."""
    _PANO_CACHE[(_round_coord(lat), _round_coord(lng))] = pano_id


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _centroid(footprint: list[list[float]]) -> tuple[float, float]:
    """Return (lat, lng) centroid of a polygon."""
    lats = [p[0] for p in footprint]
    lngs = [p[1] for p in footprint]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _bearing(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    """
    Compute the compass bearing (0-360°, clockwise from north) from one
    geographic point to another.
    """
    d_lat = math.radians(to_lat - from_lat)
    d_lng = math.radians(to_lng - from_lng)
    from_lat_r = math.radians(from_lat)
    to_lat_r = math.radians(to_lat)
    x = math.sin(d_lng) * math.cos(to_lat_r)
    y = math.cos(from_lat_r) * math.sin(to_lat_r) - math.sin(from_lat_r) * math.cos(
        to_lat_r
    ) * math.cos(d_lng)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _offset_point(
    lat: float, lng: float, bearing_deg: float, distance_m: float
) -> tuple[float, float]:
    """
    Return the geographic point reached by moving `distance_m` metres from
    (lat, lng) in direction `bearing_deg`.
    """
    R = 6_371_000.0  # Earth radius in metres
    d = distance_m / R
    b = math.radians(bearing_deg)
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)

    new_lat = math.asin(
        math.sin(lat_r) * math.cos(d) + math.cos(lat_r) * math.sin(d) * math.cos(b)
    )
    new_lng = lng_r + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lng)


_CARDINAL_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _bearing_to_cardinal(bearing: float) -> str:
    idx = round(bearing / 45) % 8
    return _CARDINAL_DIRS[idx]


# ---------------------------------------------------------------------------
# Main viewpoint calculator
# ---------------------------------------------------------------------------


def calculate_viewpoints(
    building_footprint: list[list[float]],
    epicenter_lat: float,
    epicenter_lng: float,
    standoff_m: float = 30.0,
) -> list[ScoutViewpoint]:
    """
    Compute 4-8 viewpoints positioned around a building polygon.

    Each viewpoint is placed `standoff_m` metres outside the building
    centroid, at evenly-spaced bearings.  The viewpoint facing the
    epicenter is returned first so scouts prioritise the most-at-risk side.

    Parameters
    ----------
    building_footprint:
        List of [lat, lng] polygon vertices.
    epicenter_lat / epicenter_lng:
        Location of the earthquake epicenter.
    standoff_m:
        Distance in metres from the centroid to place each viewpoint.

    Returns
    -------
    list[ScoutViewpoint] ordered with the epicenter-facing viewpoint first.
    """
    if not building_footprint:
        return []

    c_lat, c_lng = _centroid(building_footprint)

    # Determine how many sides (4 for small buildings, 8 for larger ones)
    # Estimate footprint area via bounding box; use 8 viewpoints for anything
    # larger than ~20 × 20 m.
    lat_span = max(p[0] for p in building_footprint) - min(p[0] for p in building_footprint)
    lng_span = max(p[1] for p in building_footprint) - min(p[1] for p in building_footprint)
    approx_area_m2 = (lat_span * 111_320) * (lng_span * 111_320 * math.cos(math.radians(c_lat)))
    n_viewpoints = 8 if approx_area_m2 > 400 else 4

    step = 360.0 / n_viewpoints

    # Bearing FROM centroid TOWARD epicenter — this is the side most exposed
    toward_epicenter = _bearing(c_lat, c_lng, epicenter_lat, epicenter_lng)

    # Snap to nearest multiple of `step` so viewpoints align to a grid
    start_bearing = round(toward_epicenter / step) * step % 360

    bearings = [(start_bearing + i * step) % 360 for i in range(n_viewpoints)]

    viewpoints: list[ScoutViewpoint] = []
    for b in bearings:
        vp_lat, vp_lng = _offset_point(c_lat, c_lng, b, standoff_m)
        # Heading from viewpoint back toward the building centroid
        heading_to_building = _bearing(vp_lat, vp_lng, c_lat, c_lng)
        facing = _bearing_to_cardinal(b)  # direction the scout is looking *from*
        viewpoints.append(
            ScoutViewpoint(
                lat=vp_lat,
                lng=vp_lng,
                heading=round(heading_to_building, 1),
                pitch=0.0,
                facing=facing,
            )
        )

    return viewpoints
