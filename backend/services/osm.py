"""OpenStreetMap Overpass API service for fetching building footprints."""
from __future__ import annotations

import asyncio
import logging
import math
from functools import lru_cache
from typing import Any

import httpx

from backend.models.schemas import BuildingData

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_cache: dict[tuple[float, float, float], list[BuildingData]] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_key(lat: float, lng: float, radius_m: float) -> tuple[float, float, float]:
    return (round(lat, 4), round(lng, 4), round(radius_m, 4))


def _estimate_height(levels: int | None, raw_height: str | None) -> float:
    """Return height in metres, estimating from levels when explicit height absent."""
    if raw_height:
        try:
            return float(raw_height)
        except ValueError:
            pass
    if levels:
        return levels * 3.0  # ~3 m per floor
    return 6.0  # default 2-storey


def _centroid(coords: list[list[float]]) -> tuple[float, float]:
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _derive_building_name(tags: dict[str, str], osm_id: Any) -> str:
    """Pick the best available human-readable building label."""
    for key in ("name", "official_name", "alt_name", "brand", "operator", "addr:housename"):
        value = tags.get(key)
        if value:
            return value

    street = tags.get("addr:street")
    number = tags.get("addr:housenumber")
    if street and number:
        return f"{number} {street}"
    if street:
        return street

    kind = tags.get("amenity") or tags.get("building")
    if kind and kind.lower() not in {"yes", "building", "unknown"}:
        return f"{kind.replace('_', ' ').title()} {osm_id}"

    return f"Building {osm_id}"


def _parse_element(el: dict[str, Any]) -> BuildingData | None:
    """Parse a single Overpass element (way or relation) into BuildingData."""
    tags: dict[str, str] = el.get("tags", {})
    geometry: list[dict] = el.get("geometry", [])

    if not geometry:
        return None

    footprint = [[pt["lat"], pt["lon"]] for pt in geometry if "lat" in pt and "lon" in pt]
    if len(footprint) < 3:
        return None

    lat, lng = _centroid(footprint)

    raw_levels = tags.get("building:levels") or tags.get("levels")
    try:
        levels = int(raw_levels) if raw_levels else 2
    except ValueError:
        levels = 2

    height_m = _estimate_height(levels, tags.get("height") or tags.get("building:height"))

    # Normalise material tag
    raw_material = (
        tags.get("building:material")
        or tags.get("material")
        or "unknown"
    ).lower()

    building_type = (
        tags.get("building")
        or tags.get("amenity")
        or tags.get("landuse")
        or "yes"
    )

    name = _derive_building_name(tags, el.get("id", "unknown"))

    return BuildingData(
        id=str(el["id"]),
        name=name,
        lat=lat,
        lng=lng,
        footprint=footprint,
        material=raw_material,
        levels=levels,
        height_m=height_m,
        building_type=building_type,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_buildings(
    center_lat: float,
    center_lng: float,
    radius_m: float,
) -> list[BuildingData]:
    """Fetch building footprints from Overpass API for the given area.

    Results are cached in memory keyed by (lat, lng, radius_m) rounded to 4 dp.
    Retries up to 3 times on 429/5xx with exponential back-off.
    """
    key = _round_key(center_lat, center_lng, radius_m)
    if key in _cache:
        logger.debug("OSM cache hit for key %s", key)
        return _cache[key]

    query = _build_query(center_lat, center_lng, radius_m)
    data = await _overpass_request(query)

    buildings: list[BuildingData] = []
    for el in data.get("elements", []):
        parsed = _parse_element(el)
        if parsed:
            buildings.append(parsed)

    logger.info("Fetched %d buildings from Overpass for key %s", len(buildings), key)
    _cache[key] = buildings
    return buildings


def _build_query(lat: float, lng: float, radius_m: float) -> str:
    """Build an Overpass QL query that returns buildings with full geometry."""
    return f"""
[out:json][timeout:25];
(
  way["building"](around:{radius_m},{lat},{lng});
  relation["building"](around:{radius_m},{lat},{lng});
);
out body geom;
""".strip()


async def _overpass_request(query: str, max_retries: int = 3) -> dict[str, Any]:
    """POST query to Overpass API with exponential back-off on rate-limit errors."""
    delay = 2.0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_retries):
            try:
                response = await client.post(OVERPASS_URL, data={"data": query})
                if response.status_code == 429:
                    logger.warning(
                        "Overpass rate-limited (attempt %d/%d). Sleeping %.1fs",
                        attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Overpass HTTP error %s (attempt %d/%d). Sleeping %.1fs",
                        exc.response.status_code, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logger.error("Overpass request failed after %d attempts", max_retries)
                    raise
            except httpx.RequestError as exc:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Overpass request error: %s (attempt %d/%d). Sleeping %.1fs",
                        exc, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logger.error("Overpass request error after %d attempts: %s", max_retries, exc)
                    raise
    return {}
