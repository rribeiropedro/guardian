"""
batch_vlm_analyze.py
---------------------
1. Loads vt_buildings.json + triage_scored.json
2. Joins them on building_id
3. For each building (sorted by triage score desc):
   - Fetches 1 Street View image (epicenter-facing)
   - Runs vlm_analyzer.analyze_facade()
   - Saves incremental results to vlm_results.json

Usage:
    python batch_vlm_analyze.py \
        --buildings data/vt_buildings.json \
        --triage    data/triage_scored.json \
        --output    data/vlm_results.json \
        --epicenter-lat 37.2296 \
        --epicenter-lon -80.4222 \
        --magnitude 7.2 \
        --limit 20          # optional: only process top-N by score

Requirements:
    pip install anthropic httpx python-dotenv
"""

import json
import argparse
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Import your existing vlm_analyzer functions
from vlm_analyzer import get_street_view_image, analyze_facade, get_viewpoints


# ── Helpers ────────────────────────────────────────────────────────────────

def load_json(path: str) -> list:
    with open(path, "r") as f:
        return json.load(f)


def save_json(data, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def merge_buildings(buildings: list, triage: list) -> list:
    """Join vt_buildings ← triage_scored on building_id."""
    triage_index = {rec["building_id"]: rec for rec in triage}
    merged = []
    for b in buildings:
        t = triage_index.get(b["building_id"])
        if t:
            merged.append({**b, "triage": t})
    return merged


def epicenter_distance_m(lat1, lon1, lat2, lon2) -> float:
    """Haversine distance in metres."""
    import math
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def epicenter_bearing(lat1, lon1, lat2, lon2) -> float:
    """Bearing from building to epicenter (degrees)."""
    import math
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r)*math.sin(lat2r) - math.sin(lat1r)*math.cos(lat2r)*math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


# ── Main pipeline ──────────────────────────────────────────────────────────

def run_batch(buildings_path, triage_path, output_path,
              epi_lat, epi_lon, magnitude, limit=None):

    gmaps_key = os.environ.get("GOOGLE_MAPS_KEY")
    if not gmaps_key:
        raise EnvironmentError("GOOGLE_MAPS_KEY not set in .env")

    # Load & merge
    buildings = load_json(buildings_path)
    triage    = load_json(triage_path)
    merged    = merge_buildings(buildings, triage)
    print(f"Loaded {len(merged)} matched buildings.")

    # Sort by triage score descending (highest risk first)
    merged.sort(key=lambda b: b["triage"].get("score", 0), reverse=True)

    if limit:
        merged = merged[:limit]
        print(f"Processing top {limit} buildings by triage score.")

    # Load existing results to allow resume
    if Path(output_path).exists():
        existing = load_json(output_path)
        done_ids = {r["building_id"] for r in existing}
        print(f"Resuming — {len(done_ids)} already processed.")
    else:
        existing = []
        done_ids = set()

    results = list(existing)

    for i, bldg in enumerate(merged):
        bid  = bldg["building_id"]
        name = bldg.get("name") or f"Building {bid}"

        if bid in done_ids:
            print(f"[{i+1}/{len(merged)}] ⏭  Skip {name} (already done)")
            continue

        lat = bldg.get("centroid_lat") or bldg["triage"].get("lat")
        lon = bldg.get("centroid_lon") or bldg["triage"].get("lon")

        if not lat or not lon:
            print(f"[{i+1}/{len(merged)}] ⚠  Skip {name} — no coordinates")
            continue

        dist_m   = epicenter_distance_m(lat, lon, epi_lat, epi_lon)
        bearing  = epicenter_bearing(lat, lon, epi_lat, epi_lon)

        # Get the epicenter-facing viewpoint only (first from get_viewpoints)
        views    = get_viewpoints(lat, lon, epi_lat, epi_lon)
        view     = views[0]   # primary (epicenter-facing)

        print(f"[{i+1}/{len(merged)}] 📷  {name} | score={bldg['triage']['score']:.3f} "
              f"| heading={view['heading']}° | dist={dist_m:.0f}m")

        try:
            img_b64 = get_street_view_image(lat, lon, view["heading"], gmaps_key)
        except Exception as e:
            print(f"           ❌ Street View fetch failed: {e}")
            results.append({
                "building_id": bid,
                "name": name,
                "triage": bldg["triage"],
                "vlm_error": str(e),
                "vlm_analysis": None,
            })
            save_json(results, output_path)
            continue

        ctx = {
            "building_name":     name,
            "direction":         view["label"],
            "epicenter_bearing": bearing,
            "epicenter_dist_m":  dist_m,
            "magnitude":         magnitude,
        }

        try:
            analysis = analyze_facade(img_b64, ctx)
        except Exception as e:
            print(f"           ❌ VLM analysis failed: {e}")
            analysis = None

        record = {
            "building_id":    bid,
            "name":           name,
            "centroid_lat":   lat,
            "centroid_lon":   lon,
            "material":       bldg.get("material"),
            "levels":         bldg.get("levels"),
            "height_m":       bldg.get("height_m"),
            "start_date":     bldg.get("start_date"),
            "building_type":  bldg.get("building_type"),
            "footprint":      bldg.get("footprint"),
            "triage": {
                "score":           bldg["triage"].get("score"),
                "color":           bldg["triage"].get("color"),
                "dist_km":         bldg["triage"].get("dist_km"),
                "score_breakdown": bldg["triage"].get("score_breakdown"),
            },
            "streetview": {
                "heading": view["heading"],
                "direction_label": view["label"],
                "epicenter_bearing_deg": round(bearing, 1),
                "epicenter_dist_m": round(dist_m, 1),
            },
            "vlm_analysis": analysis,
        }

        results.append(record)
        done_ids.add(bid)

        # Save after every building (crash-safe)
        save_json(results, output_path)
        print(f"           ✅ Saved. VLM risk={analysis.get('risk_level') if analysis else 'N/A'}")

        # Be polite to APIs
        time.sleep(0.5)

    print(f"\n🏁 Done. {len(results)} records saved to {output_path}")


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--buildings",     default="data/vt_buildings.json")
    parser.add_argument("--triage",        default="data/triage_scored.json")
    parser.add_argument("--output",        default="data/vlm_results.json")
    parser.add_argument("--epicenter-lat", type=float, default=37.2296)
    parser.add_argument("--epicenter-lon", type=float, default=-80.4222)
    parser.add_argument("--magnitude",     type=float, default=7.2)
    parser.add_argument("--limit",         type=int,   default=None,
                        help="Process only top-N buildings by triage score")
    args = parser.parse_args()

    run_batch(
        buildings_path = args.buildings,
        triage_path    = args.triage,
        output_path    = args.output,
        epi_lat        = args.epicenter_lat,
        epi_lon        = args.epicenter_lon,
        magnitude      = args.magnitude,
        limit          = args.limit,
    )