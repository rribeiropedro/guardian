import asyncio
import httpx
import json
import os
import math

# ── Config ────────────────────────────────────────────────────────────────────
# Manhattan midtown center point + radius
CENTER_LAT = 40.7549
CENTER_LNG = -73.9840
RADIUS_M   = 800       # ~800m radius around Times Square area → ~50+ buildings

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ── Overpass fetch ────────────────────────────────────────────────────────────
async def fetch_buildings_overpass(lat, lng, radius_m):
    query = f"""
[out:json][timeout:30];
(
  way["building"](around:{radius_m},{lat},{lng});
);
out body geom;
""".strip()

    delay = 2.0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(3):
            try:
                r = await client.post(OVERPASS_URL, data={"data": query})
                if r.status_code == 429:
                    await asyncio.sleep(delay); delay *= 2; continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt == 2: raise
                await asyncio.sleep(delay); delay *= 2
    return {}

# ── Centroid ──────────────────────────────────────────────────────────────────
def centroid(coords):
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    return sum(lats)/len(lats), sum(lngs)/len(lngs)

# ── Parse element ─────────────────────────────────────────────────────────────
def parse_element(el, index):
    tags     = el.get("tags", {})
    geometry = el.get("geometry", [])
    if not geometry:
        return None

    footprint = [[pt["lat"], pt["lon"]] for pt in geometry if "lat" in pt]
    if len(footprint) < 3:
        return None

    lat, lng = centroid(footprint)

    # Name — fall back to "Building N"
    name = (tags.get("name")
            or tags.get("addr:housename")
            or f"Building {index}")

    # Levels
    raw_levels = tags.get("building:levels") or tags.get("levels")
    try:    levels = int(raw_levels) if raw_levels else 2
    except: levels = 2

    # Height
    raw_h = tags.get("height") or tags.get("building:height")
    try:    height_m = float(raw_h)
    except: height_m = levels * 3.0

    # Material
    material = (tags.get("building:material")
                or tags.get("material")
                or "unknown").lower()

    # Building type
    building_type = (tags.get("building")
                     or tags.get("amenity")
                     or tags.get("landuse")
                     or "yes")

    # Start date
    start_date = tags.get("start_date") or tags.get("construction_date") or ""

    return {
        "building_id":   index,
        "name":          name,
        "building_type": building_type,
        "material":      material,
        "levels":        str(levels),
        "height_m":      height_m,
        "start_date":    start_date,
        "centroid_lat":  lat,
        "centroid_lon":  lng,
        "footprint":     footprint,
    }

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    os.makedirs("data", exist_ok=True)
    print(f"Fetching buildings within {RADIUS_M}m of ({CENTER_LAT}, {CENTER_LNG})...")

    data = await fetch_buildings_overpass(CENTER_LAT, CENTER_LNG, RADIUS_M)
    elements = data.get("elements", [])
    print(f"Raw elements returned: {len(elements)}")

    buildings = []
    unnamed_count = 0
    for i, el in enumerate(elements):
        parsed = parse_element(el, i + 1)
        if parsed:
            if parsed["name"].startswith("Building "):
                unnamed_count += 1
            buildings.append(parsed)

    print(f"Parsed: {len(buildings)} buildings ({unnamed_count} unnamed → auto-named)")

    with open("data/vt_buildings.json", "w") as f:
        json.dump(buildings, f, indent=2)

    print(f"Saved to data/vt_buildings.json")
    print("\nSample:")
    for b in buildings[:5]:
        print(f"  {b['name']} | {b['building_type']} | {b['material']} | "
              f"levels={b['levels']} | ({b['centroid_lat']:.4f}, {b['centroid_lon']:.4f})")

asyncio.run(main())