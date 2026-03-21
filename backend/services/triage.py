"""Triage scoring service (Task 4).

This module provides a deterministic local scorer so the pipeline can run
end-to-end until Person C's scoring service is integrated.
"""
from __future__ import annotations

import math

from ..models.schemas import BuildingData, ScoredBuilding


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_m = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(a))


def _material_vulnerability(material: str) -> float:
    m = (material or "").lower()
    if "masonry" in m or "brick" in m:
        return 1.0
    if "wood" in m or "timber" in m:
        return 0.7
    if "steel" in m:
        return 0.45
    if "concrete" in m:
        return 0.55
    return 0.65


def _estimate_occupancy(building: BuildingData, time_of_day: str) -> int:
    kind = (building.building_type or "").lower()
    base = max(10, building.levels * 25)

    if "hospital" in kind:
        base *= 3
    elif "school" in kind or "university" in kind:
        base *= 2
    elif "residential" in kind or "dorm" in kind:
        base = int(base * 1.4)
    elif "retail" in kind or "commercial" in kind:
        base = int(base * 1.2)

    if time_of_day == "night":
        if "residential" in kind or "dorm" in kind:
            base = int(base * 1.25)
        else:
            base = int(base * 0.7)

    return max(5, base)


def score_buildings(
    buildings: list[BuildingData],
    magnitude: float,
    epicenter_lat: float,
    epicenter_lng: float,
    time_of_day: str = "day",
) -> list[ScoredBuilding]:
    """Score buildings into PRD-compatible triage output.

    Returns score-sorted (descending) buildings with fields required by
    `ScoredBuilding` and wire-level `TriageResult`.
    """
    scored: list[ScoredBuilding] = []
    mag_norm = min(max((magnitude - 4.0) / 4.0, 0.0), 1.0)

    for b in buildings:
        distance_m = _haversine_m(epicenter_lat, epicenter_lng, b.lat, b.lng)
        distance_factor = max(0.0, min(1.0, 1.0 - (distance_m / 1200.0)))
        vulnerability = _material_vulnerability(b.material)
        height_factor = min(1.0, max(0.2, b.height_m / 40.0))
        occupancy = _estimate_occupancy(b, time_of_day)
        occupancy_factor = min(1.0, occupancy / 300.0)

        damage_probability = max(
            0.05,
            min(
                0.98,
                0.50 * mag_norm + 0.30 * distance_factor + 0.20 * vulnerability,
            ),
        )

        triage_score = 100.0 * (
            0.55 * damage_probability + 0.30 * occupancy_factor + 0.15 * height_factor
        )
        triage_score = max(0.0, min(100.0, triage_score))

        if triage_score >= 75:
            color = "RED"
        elif triage_score >= 55:
            color = "ORANGE"
        elif triage_score >= 35:
            color = "YELLOW"
        else:
            color = "GREEN"

        scored.append(
            ScoredBuilding(
                id=b.id,
                name=b.name,
                lat=b.lat,
                lng=b.lng,
                footprint=b.footprint,
                material=b.material,
                levels=b.levels,
                height_m=b.height_m,
                building_type=b.building_type,
                triage_score=round(triage_score, 2),
                color=color,
                damage_probability=round(damage_probability, 3),
                estimated_occupancy=occupancy,
            )
        )

    return sorted(scored, key=lambda x: x.triage_score, reverse=True)
