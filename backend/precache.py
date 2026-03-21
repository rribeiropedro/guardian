"""
Precache script for Aegis-Net demo.

Pre-fetches Street View images and panorama IDs for VT campus demo buildings
and saves them to backend/cache/ so the server can run in DEMO_MODE without
making live Street View API calls.

Usage:
    cd aegis-net
    GOOGLE_MAPS_API_KEY=<key> python -m backend.precache

Output:
    backend/cache/images/<lat>_<lng>_<heading>.jpg  — Street View JPEG images
    backend/cache/panos.json                         — {lat,lng -> pano_id} index
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
_CACHE_DIR = _ROOT / "cache"
_IMAGE_DIR = _CACHE_DIR / "images"
_PANO_INDEX_FILE = _CACHE_DIR / "panos.json"

# ---------------------------------------------------------------------------
# VT campus demo buildings  (lat, lng, name)
# ---------------------------------------------------------------------------

DEMO_BUILDINGS: list[tuple[float, float, str]] = [
    (37.2296, -80.4236, "Burruss Hall"),
    (37.2274, -80.4240, "Torgersen Hall"),
    (37.2282, -80.4228, "Newman Library"),
    (37.2291, -80.4248, "Squires Student Center"),
    (37.2265, -80.4232, "Randolph Hall"),
    (37.2302, -80.4220, "Whittemore Hall"),
    (37.2278, -80.4255, "War Memorial Hall"),
]

STREET_VIEW_BASE = "https://maps.googleapis.com/maps/api/streetview"
STREET_VIEW_META = "https://maps.googleapis.com/maps/api/streetview/metadata"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _bearing(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    d_lng = math.radians(to_lng - from_lng)
    from_lat_r = math.radians(from_lat)
    to_lat_r = math.radians(to_lat)
    x = math.sin(d_lng) * math.cos(to_lat_r)
    y = math.cos(from_lat_r) * math.sin(to_lat_r) - math.sin(from_lat_r) * math.cos(to_lat_r) * math.cos(d_lng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _offset_point(lat: float, lng: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    R = 6_371_000.0
    d = distance_m / R
    b = math.radians(bearing_deg)
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    new_lat = math.asin(math.sin(lat_r) * math.cos(d) + math.cos(lat_r) * math.sin(d) * math.cos(b))
    new_lng = lng_r + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lng)


def _viewpoints_for_building(lat: float, lng: float, n: int = 4, standoff_m: float = 30.0) -> list[tuple[float, float, float]]:
    """Return (vp_lat, vp_lng, heading_toward_building) for n evenly-spaced viewpoints."""
    step = 360.0 / n
    results = []
    for i in range(n):
        bearing = (i * step) % 360
        vp_lat, vp_lng = _offset_point(lat, lng, bearing, standoff_m)
        heading = _bearing(vp_lat, vp_lng, lat, lng)
        results.append((vp_lat, vp_lng, round(heading, 1)))
    return results


def _image_cache_key(lat: float, lng: float, heading: float) -> str:
    return f"{lat:.6f}_{lng:.6f}_{heading:.1f}"


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_pano_id(client: httpx.AsyncClient, lat: float, lng: float, api_key: str) -> str | None:
    params = {"location": f"{lat},{lng}", "key": api_key}
    try:
        r = await client.get(STREET_VIEW_META, params=params, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "OK":
            return data["pano_id"]
        logger.debug("No pano at (%.6f, %.6f): %s", lat, lng, data.get("status"))
    except Exception as exc:
        logger.warning("Pano lookup failed for (%.6f, %.6f): %s", lat, lng, exc)
    return None


async def _fetch_image(
    client: httpx.AsyncClient,
    lat: float,
    lng: float,
    heading: float,
    api_key: str,
    size: str = "640x640",
) -> bytes | None:
    params = {
        "location": f"{lat},{lng}",
        "heading": str(heading),
        "pitch": "0",
        "fov": "90",
        "size": size,
        "key": api_key,
    }
    try:
        r = await client.get(STREET_VIEW_BASE, params=params, timeout=10.0)
        r.raise_for_status()
        # Street View returns a grey placeholder for locations with no imagery
        if r.headers.get("content-type", "").startswith("image/jpeg") and r.content[:2] == b"\xff\xd8":
            return r.content
        logger.debug("Got non-JPEG or placeholder response at (%.6f, %.6f, %.1f°)", lat, lng, heading)
    except Exception as exc:
        logger.warning("Image fetch failed for (%.6f, %.6f, %.1f°): %s", lat, lng, heading, exc)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def precache(api_key: str) -> None:
    _CACHE_DIR.mkdir(exist_ok=True)
    _IMAGE_DIR.mkdir(exist_ok=True)

    pano_index: dict[str, str] = {}
    if _PANO_INDEX_FILE.exists():
        try:
            pano_index = json.loads(_PANO_INDEX_FILE.read_text())
        except Exception:
            pass

    image_count = 0
    pano_count = 0

    async with httpx.AsyncClient() as client:
        for (b_lat, b_lng, name) in DEMO_BUILDINGS:
            logger.info("Processing: %s (%.4f, %.4f)", name, b_lat, b_lng)
            viewpoints = _viewpoints_for_building(b_lat, b_lng, n=4)

            for vp_lat, vp_lng, heading in viewpoints:
                # --- panorama ID for viewpoint location ---
                pano_key = f"{round(vp_lat, 4)},{round(vp_lng, 4)}"
                if pano_key not in pano_index:
                    pano_id = await _fetch_pano_id(client, vp_lat, vp_lng, api_key)
                    if pano_id:
                        pano_index[pano_key] = pano_id
                        pano_count += 1
                        logger.info("  pano cached: %s → %s", pano_key, pano_id)

                # --- panorama ID for building centroid (used for route waypoints) ---
                centroid_key = f"{round(b_lat, 4)},{round(b_lng, 4)}"
                if centroid_key not in pano_index:
                    pano_id = await _fetch_pano_id(client, b_lat, b_lng, api_key)
                    if pano_id:
                        pano_index[centroid_key] = pano_id
                        pano_count += 1

                # --- Street View image ---
                img_key = _image_cache_key(vp_lat, vp_lng, heading)
                img_path = _IMAGE_DIR / f"{img_key}.jpg"
                if img_path.exists():
                    logger.debug("  image already cached: %s", img_key)
                    continue
                img_bytes = await _fetch_image(client, vp_lat, vp_lng, heading, api_key)
                if img_bytes:
                    img_path.write_bytes(img_bytes)
                    image_count += 1
                    logger.info("  image saved: %s (%d bytes)", img_key, len(img_bytes))

    # Persist pano index
    _PANO_INDEX_FILE.write_text(json.dumps(pano_index, indent=2))
    logger.info(
        "Precache complete: %d new images, %d new pano IDs. Index has %d entries.",
        image_count, pano_count, len(pano_index),
    )


if __name__ == "__main__":
    import os
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        print("ERROR: GOOGLE_MAPS_API_KEY env var not set.", file=sys.stderr)
        sys.exit(1)
    asyncio.run(precache(key))
