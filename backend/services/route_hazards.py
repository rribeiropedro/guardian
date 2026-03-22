"""
Route hazard zone modeling for earthquake emergency routing.

Research basis:
  - Debris fall zone: 1.5× building height in all directions (FEMA P-154, ATC-20).
    Extended to 2.0× for RED-triaged buildings and to 1.8× for masonry/brick,
    which produces the most lethal fragmentation (rigid + high dispersal).
  - Material multipliers validated by post-earthquake debris studies:
      masonry/brick  → 1.8× (worst fragmentation, highest lethality)
      concrete       → 1.2× (heavy chunks, moderate dispersal)
      steel          → 0.7× (bends rather than fragments, minimal debris)
      wood/other     → 1.0× (baseline)
  - Triage-color radius scaling accounts for progressive damage:
      RED    → 2.0× (near-certain collapse hazard)
      ORANGE → 1.5× (partial failure likely)
      YELLOW → 1.0× (moderate risk)
      GREEN  → 0.5× (low risk; still maintain clearance)
  - Cost function uses damage_probability × (1 − dist/radius) so cost
    falls off linearly with distance inside the zone.  Combined with the
    exposure-time model (MDPI 2020): total_cost ≈ distance + Σ hazard_exposure.
  - Scout external_risk records (direction, estimated_range_m) are treated as
    point-source hazard zones independent of building footprints.
  - Scout structural/overhead CRITICAL findings block the zone entirely
    (cost = infinity); MODERATE = 3.0× multiplier; LOW = 1.1×.

Public API consumed by route.py:
  build_hazard_zones(buildings, records, epicenter_lat, epicenter_lng, magnitude)
      → list[HazardZone]
  waypoint_cost(lat, lng, zones) → float      # 0 = safe, >0 accumulates
  classify_waypoint_hazard(lat, lng, zones) → Hazard | None
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from ..models.schemas import Hazard, ScoredBuilding


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HazardZone:
    """A circular exclusion / penalty zone centered on a geographic point.

    Fields
    ------
    center_lat / center_lng:
        Center of the zone (building centroid or scout external-risk origin).
    radius_m:
        Outer edge of the penalty zone in metres (1.5H × material × color).
    hard_block_radius_m:
        Inner "no-go" radius — anything inside is treated as cost=∞.
        For scout CRITICAL findings this equals radius_m (full block).
        For building zones this is typically 0.5 × radius_m (inner core).
    damage_probability:
        From ScoredBuilding or assumed 1.0 for CRITICAL scout findings.
        Scales the penalty gradient so a higher-probability building costs more.
    cost_multiplier:
        Additional flat multiplier applied to any edge passing through this zone.
        Derived from scout finding severity:
            CRITICAL structural/overhead → math.inf (or a large finite value)
            CRITICAL route obstruction   → math.inf
            MODERATE                     → 3.0
            LOW                          → 1.1
        Building-only zones use 1.0 (cost_multiplier handled by gradient alone).
    label:
        Human-readable description of the source (used for Waypoint.hazard.label).
    hazard_type:
        Waypoint hazard type emitted to the frontend.
    source:
        "building" | "scout_external" | "scout_finding"
    """
    center_lat: float
    center_lng: float
    radius_m: float
    hard_block_radius_m: float
    damage_probability: float
    cost_multiplier: float
    label: str
    hazard_type: Literal["blocked", "overhead", "turn", "arrival", "intel", "medical"]
    source: Literal["building", "scout_external", "scout_finding"]
    color: str = "red"  # hex or CSS color for frontend


# ---------------------------------------------------------------------------
# Radius helpers
# ---------------------------------------------------------------------------

# FEMA P-154 / ATC-20 baseline: debris lands up to 1.5× building height away.
_BASE_DEBRIS_FACTOR = 1.5

_MATERIAL_MULTIPLIER: dict[str, float] = {
    # Unreinforced masonry / brick: rigid + high fragmentation; worst case.
    "masonry":  1.8,
    "brick":    1.8,
    # Non-ductile concrete: heavy chunks, moderate dispersal.
    "concrete": 1.2,
    # Steel: bends rather than explodes; far lower debris throw.
    "steel":    0.7,
    # Wood / timber: lightweight splinters; lower lethality.
    "wood":     1.0,
    "timber":   1.0,
}

_COLOR_RADIUS_SCALE: dict[str, float] = {
    # RED: near-certain collapse hazard — extend zone to 2× FEMA baseline.
    "RED":    2.0,
    # ORANGE: partial failure likely.
    "ORANGE": 1.5,
    # YELLOW: moderate risk.
    "YELLOW": 1.0,
    # GREEN: low risk; retain a minimal clearance.
    "GREEN":  0.5,
}

# Minimum enforced radius even for very short buildings.
_MIN_RADIUS_M = 5.0


def _material_mult(material: str) -> float:
    """Return the debris-dispersal multiplier for a building material string."""
    m = (material or "").lower()
    for key, mult in _MATERIAL_MULTIPLIER.items():
        if key in m:
            return mult
    return 1.0  # default / unknown


def debris_radius_m(building: ScoredBuilding) -> float:
    """Calculate the outer debris-hazard radius for a building.

    Formula:
        radius = max(MIN_RADIUS_M,
                     height_m × BASE_FACTOR × material_mult × color_scale)

    The inner hard-block radius is 0.5 × radius (inner collapse core where
    approach is unconditionally unsafe for non-USAR personnel).
    """
    radius = (
        building.height_m
        * _BASE_DEBRIS_FACTOR
        * _material_mult(building.material)
        * _COLOR_RADIUS_SCALE.get(building.color, 1.0)
    )
    return max(_MIN_RADIUS_M, radius)


# ---------------------------------------------------------------------------
# Zone builders
# ---------------------------------------------------------------------------

def _building_zone(building: ScoredBuilding) -> HazardZone:
    """Convert a ScoredBuilding into a HazardZone.

    The cost_multiplier is 1.0 here because the penalty gradient
    (damage_probability × (1 − dist/radius)) already captures severity.
    Scout findings on the same building may add additional multipliers.
    """
    radius = debris_radius_m(building)
    # Color → human label
    color_label = {
        "RED": "#ef4444",
        "ORANGE": "#f97316",
        "YELLOW": "#eab308",
        "GREEN": "#22c55e",
    }.get(building.color, "#6b7280")

    hazard_type: Literal["blocked", "overhead", "turn", "arrival", "intel", "medical"]
    if building.color == "RED":
        hazard_type = "blocked"
    elif building.color == "ORANGE":
        hazard_type = "overhead"
    else:
        hazard_type = "intel"

    return HazardZone(
        center_lat=building.lat,
        center_lng=building.lng,
        radius_m=radius,
        hard_block_radius_m=radius * 0.5,
        damage_probability=building.damage_probability,
        cost_multiplier=1.0,
        label=f"{building.name} ({building.color}) — debris zone {radius:.0f}m",
        hazard_type=hazard_type,
        source="building",
        color=color_label,
    )


def _scout_external_zone(
    origin_lat: float,
    origin_lng: float,
    risk_type: str,
    estimated_range_m: float,
    scout_id: str,
    building_id: str,
) -> HazardZone:
    """Convert a SharedState ExternalRisk record into a HazardZone.

    Scout external risks (e.g. "structural hazard to the north, 150 m range")
    are treated as point-source hazards centred on the reporting building.
    They are assigned full damage_probability=1.0 (the scout has confirmed
    the hazard visually via VLM) and a hard_block_radius = 0.3 × range.
    """
    # TODO: map risk_type string to hazard_type enum
    # Possible risk_type values from VLM: "structural", "fire", "utility",
    # "debris", "overhead", "flood", etc.  Default to "blocked".
    hazard_type: Literal["blocked", "overhead", "turn", "arrival", "intel", "medical"] = "blocked"
    if "overhead" in risk_type.lower() or "power" in risk_type.lower():
        hazard_type = "overhead"

    return HazardZone(
        center_lat=origin_lat,
        center_lng=origin_lng,
        radius_m=estimated_range_m,
        hard_block_radius_m=estimated_range_m * 0.3,
        damage_probability=1.0,
        cost_multiplier=1.0,
        label=f"Scout {scout_id} — {risk_type} from building {building_id}",
        hazard_type=hazard_type,
        source="scout_external",
        color="#dc2626",
    )


# severity → (cost_multiplier, hard_block)
_SEVERITY_COST: dict[str, tuple[float, bool]] = {
    "CRITICAL": (math.inf, True),
    "MODERATE": (3.0, False),
    "LOW":      (1.1, False),
}

# finding category → hazard_type
_CATEGORY_HAZARD: dict[str, Literal["blocked", "overhead", "turn", "arrival", "intel", "medical"]] = {
    "structural": "blocked",
    "overhead":   "overhead",
    "route":      "blocked",
    "access":     "intel",
}

# Per-category penalty radii around the source building centroid (metres).
# These are conservative; route-category findings block a broader corridor.
_CATEGORY_RADIUS: dict[str, float] = {
    "structural": 30.0,  # Use in addition to debris_radius_m; represents confirmed collapse hazard
    "overhead":   20.0,  # Falling facade / canopy / power lines above
    "route":      50.0,  # Confirmed blockage on path — wider exclusion
    "access":     10.0,  # Access point blocked; small zone
}


def _scout_finding_zone(
    building: ScoredBuilding,
    category: str,
    severity: str,
) -> HazardZone:
    """Convert a scout VLM Finding into a HazardZone around its source building.

    CRITICAL findings trigger a hard block (cost = ∞) within the zone.
    MODERATE/LOW add a cost multiplier instead.

    Parameters
    ----------
    building:
        The building the scout was analyzing when it reported the finding.
    category:
        "structural" | "access" | "overhead" | "route"
    severity:
        "CRITICAL" | "MODERATE" | "LOW"
    """
    multiplier, is_hard_block = _SEVERITY_COST.get(severity, (1.0, False))
    hazard_type = _CATEGORY_HAZARD.get(category, "intel")
    radius = _CATEGORY_RADIUS.get(category, 20.0)

    # For structural CRITICAL, overlay on top of existing debris zone
    # by using the larger of the two radii.
    if category == "structural" and severity == "CRITICAL":
        radius = max(radius, debris_radius_m(building))

    return HazardZone(
        center_lat=building.lat,
        center_lng=building.lng,
        radius_m=radius,
        hard_block_radius_m=radius if is_hard_block else radius * 0.3,
        damage_probability=1.0 if is_hard_block else building.damage_probability,
        cost_multiplier=multiplier,
        label=f"Scout finding: {severity} {category} at {building.name}",
        hazard_type=hazard_type,
        source="scout_finding",
        color="#991b1b" if is_hard_block else "#f97316",
    )


def build_hazard_zones(
    buildings: list[ScoredBuilding],
    # Records are _RiskRecord-like objects from SharedState.query_nearby();
    # typed as object to avoid circular import — access attributes by name.
    shared_state_records: list[object],
    # scout_findings maps building_id → list of (category, severity) tuples
    # gathered from scout VLM reports stored in SharedState or passed down.
    # TODO: decide with Person C how scout Finding objects are persisted and
    # made accessible here — options:
    #   (a) extend SharedState.write_findings to also store Finding objects, or
    #   (b) pass findings dict explicitly from _handle_request_route in main.py.
    scout_findings_by_building: dict[str, list[tuple[str, str]]] | None = None,
    epicenter_lat: float = 0.0,
    epicenter_lng: float = 0.0,
    magnitude: float = 6.0,
) -> list[HazardZone]:
    """Build the full hazard-zone list from all available data sources.

    Data sources (in priority order):
    1. ScoredBuilding entries → debris zones based on height / material / triage.
    2. SharedState ExternalRisk records → directional hazards from scout VLM.
    3. Scout VLM Findings → severity-weighted zones around analyzed buildings.

    Magnitude scaling:
        Zones are scaled by a mild factor of (1 + 0.1 × (magnitude - 6))
        so an M8 earthquake produces ~20% wider effective exclusion radii
        and an M5 produces ~10% narrower ones.  This is an approximation —
        the core 1.5H rule already captures building-specific vulnerability.

    Returns
    -------
    list[HazardZone] — may be passed directly to waypoint_cost() and
    classify_waypoint_hazard().
    """
    # TODO: clamp magnitude scaling to [0.8, 1.4] so it does not dominate.
    mag_scale = 1.0 + 0.1 * (magnitude - 6.0)
    mag_scale = max(0.8, min(1.4, mag_scale))

    zones: list[HazardZone] = []

    # --- 1. Building debris zones ---
    for b in buildings:
        zone = _building_zone(b)
        # Apply magnitude scaling to radius.
        zone.radius_m *= mag_scale
        zone.hard_block_radius_m *= mag_scale
        zones.append(zone)

    # --- 2. Scout external-risk zones ---
    for record in shared_state_records:
        zone = _scout_external_zone(
            origin_lat=record.origin_lat,          # type: ignore[attr-defined]
            origin_lng=record.origin_lng,          # type: ignore[attr-defined]
            risk_type=record.risk_type,            # type: ignore[attr-defined]
            estimated_range_m=record.estimated_range_m,  # type: ignore[attr-defined]
            scout_id=record.scout_id,              # type: ignore[attr-defined]
            building_id=record.building_id,        # type: ignore[attr-defined]
        )
        zones.append(zone)

    # --- 3. Scout VLM finding zones ---
    if scout_findings_by_building:
        building_map = {b.id: b for b in buildings}
        for building_id, findings in scout_findings_by_building.items():
            b = building_map.get(building_id)
            if b is None:
                continue
            for category, severity in findings:
                zones.append(_scout_finding_zone(b, category, severity))

    return zones


# ---------------------------------------------------------------------------
# Cost and classification helpers (called per waypoint in route.py)
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def waypoint_cost(lat: float, lng: float, zones: list[HazardZone]) -> float:
    """Return the aggregate hazard cost at a geographic point.

    Cost model (per zone):
        if dist ≤ hard_block_radius_m:
            cost += math.inf  →  route.py will skip or heavily penalize this point
        elif dist < radius_m:
            # Linear gradient: full cost at center, zero at outer edge.
            gradient = 1.0 - (dist / radius_m)
            cost += damage_probability × gradient × cost_multiplier
        else:
            cost += 0.0

    The caller (route.py) adds this to the base travel-time cost when scoring
    candidate paths.  A combined weight α=1.0 (distance) + β=2.0 (hazard) is
    appropriate for first-responder rescue routing per MDPI 2020 research.
    Adjust β in route.py's _score_path to tune risk tolerance.

    Returns math.inf if the point is inside ANY hard-block zone.
    """
    total_cost = 0.0
    for zone in zones:
        dist = _haversine_m(lat, lng, zone.center_lat, zone.center_lng)
        if dist <= zone.hard_block_radius_m:
            # TODO: decide whether to return immediately or accumulate.
            # Returning immediately is slightly faster; accumulating lets
            # the caller distinguish "one critical block" vs "many".
            return math.inf
        if dist < zone.radius_m:
            gradient = 1.0 - (dist / zone.radius_m)
            multiplier = zone.cost_multiplier if zone.cost_multiplier != math.inf else 10.0
            total_cost += zone.damage_probability * gradient * multiplier
    return total_cost


def classify_waypoint_hazard(
    lat: float,
    lng: float,
    zones: list[HazardZone],
) -> Hazard | None:
    """Return the most significant Hazard at a waypoint, or None if safe.

    Used to populate Waypoint.hazard in route.py so the frontend can render
    hazard icons along the route.

    Priority: hard-block zones first → then by highest cost_multiplier →
    then by closest outer-zone hit.

    Returns None when the point has zero hazard cost (fully outside all zones).
    """
    best_zone: HazardZone | None = None
    best_priority = -1.0

    for zone in zones:
        dist = _haversine_m(lat, lng, zone.center_lat, zone.center_lng)
        if dist >= zone.radius_m:
            continue
        # Priority: hard-block wins, then cost_multiplier, then proximity.
        is_hard_block = dist <= zone.hard_block_radius_m
        priority = (
            (1_000.0 if is_hard_block else 0.0)
            + (zone.cost_multiplier if zone.cost_multiplier != math.inf else 500.0)
            + (1.0 - dist / zone.radius_m)  # closer = higher priority
        )
        if priority > best_priority:
            best_priority = priority
            best_zone = zone

    if best_zone is None:
        return None

    return Hazard(
        type=best_zone.hazard_type,
        color=best_zone.color,
        label=best_zone.label,
    )
