"""Route calculation service — Task 8 (hazard-aware revision).

Algorithm: Modified Dijkstra on a sparse grid of candidate waypoints.

Why Dijkstra (not A*):
    Scout findings arrive asynchronously and dynamically update the hazard map.
    Dijkstra handles non-uniform, real-time edge weights cleanly. A*'s Euclidean
    heuristic breaks when hazard multipliers make the "short" path very expensive.
    Reference: "Balancing Hazard Exposure and Walking Distance in Evacuation Route
    Planning" (MDPI 2020) and the SpeedGuides evacuation system both use Dijkstra
    with dynamic edge re-weighting.

Grid approach vs. street graph:
    We do not have a routable street graph from OSM (only building footprints).
    Instead we generate a sparse grid of candidate nodes between start and target,
    connect adjacent nodes into a graph, weight edges with the hazard cost model
    from route_hazards.py, and run Dijkstra.  Straight-line distance is the
    fallback when the grid finds no improvement over the direct path.

Cost function (per MDPI 2020 research):
    total_edge_cost = (edge_length_m / WALK_SPEED_MPS)     ← travel time
                    + HAZARD_BETA × waypoint_cost(mid_lat, mid_lng, zones)

    HAZARD_BETA = 2.0  → hazard exposure weighted 2× vs. travel time.
    For first-responder / rescue use (vs. civilian evacuation) this is a
    reasonable balance: responders accept a longer path for a safer one, but
    not an arbitrarily long detour. Adjust via ROUTE_HAZARD_BETA env var.

Information required from other modules
----------------------------------------
From main.py / _handle_request_route (NEW fields to add):
    epicenter_lat, epicenter_lng  — currently in _scenario_state but not passed
    magnitude                     — same
    shared_state_records          — from get_shared_state().query_nearby() or all records
    scout_findings_by_building    — TODO: needs persistence decision (see route_hazards.py)

From ScoredBuilding (already in hazard_buildings):
    lat, lng, height_m, material, triage_score, color, damage_probability, footprint

From SharedState (needs to be pulled in route handler):
    _RiskRecord objects from get_shared_state()._records (or a new public getter)
    TODO: add SharedState.get_all_records() → list[_RiskRecord] method

Public API (unchanged contract with frontend/main.py):
    async def calculate_route(
        start, target_building, hazard_buildings,
        epicenter_lat, epicenter_lng, magnitude,         ← NEW
        shared_state_records=None,                       ← NEW
        scout_findings_by_building=None,                 ← NEW
    ) → list[Waypoint]
"""
from __future__ import annotations

import asyncio
import heapq
import math
from typing import Iterator

from ..models.schemas import ScoredBuilding, Waypoint
from . import streetview
from .route_hazards import (
    HazardZone,
    build_hazard_zones,
    classify_waypoint_hazard,
    waypoint_cost,
)

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

STEP_M: float = 50.0          # Baseline waypoint spacing along the route (metres)
GRID_COLS: int = 7             # Number of lateral grid columns either side of straight line
GRID_LATERAL_SPREAD_M: float = 120.0  # Max lateral offset from straight-line path (metres)
WALK_SPEED_MPS: float = 1.4   # Pedestrian speed (m/s); 1.4 m/s is standard evacuation speed

# Hazard beta: relative weight of hazard cost vs. travel time.
# 2.0 = first-responder routing (MDPI 2020 recommendation for rescue teams).
# Increase toward 5.0 for pure civilian evacuation.
HAZARD_BETA: float = 2.0

# Cost assigned to waypoints inside the hard-block radius — treated as passable
# but extremely expensive (avoids discontinuous ∞ in Dijkstra graph).
HARD_BLOCK_COST: float = 1_000.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    d_lng = math.radians(to_lng - from_lng)
    from_lat_r = math.radians(from_lat)
    to_lat_r = math.radians(to_lat)
    x = math.sin(d_lng) * math.cos(to_lat_r)
    y = (math.cos(from_lat_r) * math.sin(to_lat_r)
         - math.sin(from_lat_r) * math.cos(to_lat_r) * math.cos(d_lng))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _offset_point(lat: float, lng: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Move distance_m metres from (lat, lng) along bearing_deg."""
    R = 6_371_000.0
    b = math.radians(bearing_deg)
    lat_r = math.radians(lat)
    lng_r = math.radians(lng)
    d = distance_m / R
    new_lat_r = math.asin(
        math.sin(lat_r) * math.cos(d) + math.cos(lat_r) * math.sin(d) * math.cos(b)
    )
    new_lng_r = lng_r + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(new_lat_r),
    )
    return math.degrees(new_lat_r), math.degrees(new_lng_r)


def _centroid(footprint: list[list[float]]) -> tuple[float, float]:
    lats = [p[0] for p in footprint]
    lngs = [p[1] for p in footprint]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def _generate_grid(
    start: tuple[float, float],
    target: tuple[float, float],
    step_m: float = STEP_M,
    cols: int = GRID_COLS,
    lateral_spread_m: float = GRID_LATERAL_SPREAD_M,
) -> list[tuple[float, float]]:
    """Generate a 2-D grid of candidate waypoints between start and target.

    Layout
    ------
    The grid is oriented along the start→target axis.
    Rows are spaced step_m apart along that axis.
    Each row has (2×cols + 1) nodes spaced evenly across [-lateral_spread_m,
    +lateral_spread_m] perpendicular to the axis.

    The straight-line column (col=0) is always included, ensuring the classic
    50 m sample path is a valid Dijkstra candidate even when no detour is needed.

    Parameters
    ----------
    start / target:
        (lat, lng) endpoints.
    step_m:
        Row spacing along the forward axis (metres).
    cols:
        Number of lateral columns each side of the centre line.
        Total columns per row = 2×cols + 1.
    lateral_spread_m:
        Total lateral width of the grid (metres).  Nodes are placed at
        offsets: -lateral_spread_m, …, 0, …, +lateral_spread_m.

    Returns
    -------
    List of (lat, lng) tuples including start and target.  Duplicates
    within 1 m are deduplicated in _build_graph.
    """
    start_lat, start_lng = start
    target_lat, target_lng = target

    total_dist = _haversine_m(start_lat, start_lng, target_lat, target_lng)
    if total_dist < 1.0:
        return [start, target]

    forward_bearing = _bearing(start_lat, start_lng, target_lat, target_lng)
    right_bearing = (forward_bearing + 90.0) % 360.0  # perpendicular right

    num_rows = max(1, int(total_dist / step_m))
    lateral_offsets = [
        (i / cols) * lateral_spread_m
        for i in range(-cols, cols + 1)
    ] if cols > 0 else [0.0]

    nodes: list[tuple[float, float]] = [start]

    for row in range(1, num_rows):
        forward_dist = (row / num_rows) * total_dist
        row_center = _offset_point(start_lat, start_lng, forward_bearing, forward_dist)

        for offset in lateral_offsets:
            if abs(offset) < 0.01:
                nodes.append(row_center)
            else:
                side_bearing = right_bearing if offset > 0 else (right_bearing + 180) % 360
                node = _offset_point(row_center[0], row_center[1], side_bearing, abs(offset))
                nodes.append(node)

    nodes.append(target)
    return nodes


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

# Max edge length to consider two grid nodes "connected" (metres).
# Set to 1.5× step so diagonal connections are included.
_MAX_EDGE_M = STEP_M * 1.8


def _build_graph(
    nodes: list[tuple[float, float]],
) -> dict[int, list[tuple[int, float]]]:
    """Build an adjacency list connecting nearby grid nodes.

    Edges connect any two nodes within _MAX_EDGE_M of each other.
    Edge weight is raw Euclidean distance in metres (hazard cost is added
    by _dijkstra when traversing each edge).

    Returns
    -------
    dict mapping node_index → [(neighbour_index, distance_m), ...]
    """
    n = len(nodes)
    graph: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}

    # O(n²) — acceptable for n ≤ ~500 (100 m route / 50 m step × 15 columns).
    # TODO: if grids become large, replace with a spatial index (e.g. KD-tree).
    for i in range(n):
        for j in range(i + 1, n):
            dist = _haversine_m(nodes[i][0], nodes[i][1], nodes[j][0], nodes[j][1])
            if dist <= _MAX_EDGE_M:
                graph[i].append((j, dist))
                graph[j].append((i, dist))

    return graph


# ---------------------------------------------------------------------------
# Dijkstra with hazard-weighted edges
# ---------------------------------------------------------------------------

def _edge_cost(
    from_node: tuple[float, float],
    to_node: tuple[float, float],
    edge_dist_m: float,
    zones: list[HazardZone],
) -> float:
    """Compute the total cost of traversing one graph edge.

    Cost = travel_time + HAZARD_BETA × hazard_at_midpoint

    The midpoint is used as a representative hazard sample for the edge.
    For very long edges a multi-sample approach would be more accurate —
    TODO: consider sampling at 25% / 50% / 75% along the edge and taking max.

    Hard-block zones return HARD_BLOCK_COST (large finite value) so Dijkstra
    can still find a path through them as a last resort rather than failing.
    """
    travel_time = edge_dist_m / WALK_SPEED_MPS

    mid_lat = (from_node[0] + to_node[0]) / 2.0
    mid_lng = (from_node[1] + to_node[1]) / 2.0
    haz = waypoint_cost(mid_lat, mid_lng, zones)

    if math.isinf(haz):
        haz = HARD_BLOCK_COST

    return travel_time + HAZARD_BETA * haz


def _dijkstra(
    nodes: list[tuple[float, float]],
    graph: dict[int, list[tuple[int, float]]],
    start_idx: int,
    end_idx: int,
    zones: list[HazardZone],
) -> list[int]:
    """Modified Dijkstra — returns the ordered list of node indices on the best path.

    Edge weights include both travel-time and hazard exposure (see _edge_cost).
    Returns [start_idx, ..., end_idx].  Returns [start_idx, end_idx] (direct)
    if no path is found (should not happen on a well-connected grid).
    """
    dist: dict[int, float] = {start_idx: 0.0}
    prev: dict[int, int | None] = {start_idx: None}
    heap: list[tuple[float, int]] = [(0.0, start_idx)]

    while heap:
        cost, u = heapq.heappop(heap)
        if u == end_idx:
            break
        if cost > dist.get(u, math.inf):
            continue
        for v, edge_dist in graph.get(u, []):
            new_cost = cost + _edge_cost(nodes[u], nodes[v], edge_dist, zones)
            if new_cost < dist.get(v, math.inf):
                dist[v] = new_cost
                prev[v] = u
                heapq.heappush(heap, (new_cost, v))

    # Reconstruct path
    path: list[int] = []
    current: int | None = end_idx
    while current is not None:
        path.append(current)
        current = prev.get(current)
    path.reverse()

    if not path or path[0] != start_idx:
        return [start_idx, end_idx]  # fallback: direct edge
    return path


# ---------------------------------------------------------------------------
# Straight-line fallback (original Task 8 behavior)
# ---------------------------------------------------------------------------

def _straight_line_samples(
    start: tuple[float, float],
    target: tuple[float, float],
    step_m: float = STEP_M,
) -> list[tuple[float, float]]:
    """Return evenly-spaced samples along the straight-line path."""
    start_lat, start_lng = start
    target_lat, target_lng = target
    total_dist = _haversine_m(start_lat, start_lng, target_lat, target_lng)
    if total_dist < 1.0:
        return []
    num_steps = max(1, int(total_dist / step_m))
    route_bearing = _bearing(start_lat, start_lng, target_lat, target_lng)
    return [
        _offset_point(start_lat, start_lng, route_bearing, (i / num_steps) * total_dist)
        for i in range(num_steps + 1)
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def calculate_route(
    start: tuple[float, float],
    target_building: ScoredBuilding,
    hazard_buildings: list[ScoredBuilding],
    # --- New parameters for hazard-aware routing ---
    epicenter_lat: float = 0.0,
    epicenter_lng: float = 0.0,
    magnitude: float = 6.0,
    # SharedState _RiskRecord objects; typed as list[object] to avoid circular import.
    # Caller: get_shared_state()._records  (or add get_all_records() to SharedState).
    # TODO: confirm with Person B / main.py that this is the right call site.
    shared_state_records: list[object] | None = None,
    # dict[building_id → list[(category, severity)]] from persisted scout findings.
    # TODO: needs a persistence mechanism — see route_hazards.py docstring.
    scout_findings_by_building: dict[str, list[tuple[str, str]]] | None = None,
) -> list[Waypoint]:
    """Calculate a hazard-aware walking route from start to target_building.

    Algorithm summary
    -----------------
    1. Build hazard zones from buildings, scout external-risk records, and
       scout VLM findings (route_hazards.build_hazard_zones).
    2. Generate a sparse 2-D candidate grid between start and target centroid.
    3. Connect grid nodes into a graph (edges ≤ 1.8 × STEP_M).
    4. Run modified Dijkstra: edge cost = travel_time + HAZARD_BETA × hazard_cost.
    5. Extract the best-path node sequence.
    6. Fetch Street View panorama IDs for each path node concurrently.
    7. Skip nodes with no pano coverage; annotate survivors with Waypoint.hazard.
    8. Fall back to straight-line path if Dijkstra returns the direct edge only.

    Backward-compatible contract
    ----------------------------
    The function signature extends (not replaces) the original Task 8 signature.
    main.py's _handle_request_route currently calls:
        waypoints = await calculate_route(start, target, hazard_buildings)
    Adding the new keyword arguments does not break this call; defaults produce
    the original behavior with minimal hazard info.

    TODO items before this is production-ready
    ------------------------------------------
    - _handle_request_route in main.py must pass epicenter_lat/lng and magnitude
      from _scenario_state (they are already stored there).
    - SharedState needs get_all_records() or the route handler calls
      get_shared_state()._records directly (acceptable for now).
    - scout_findings_by_building population path needs a decision from Person C
      (see route_hazards.py for options).
    """

    target_lat, target_lng = _centroid(target_building.footprint)
    start_lat, start_lng = start

    total_dist = _haversine_m(start_lat, start_lng, target_lat, target_lng)
    if total_dist < 1.0:
        return []

    # --- Step 1: Build hazard zones ---
    zones = build_hazard_zones(
        buildings=hazard_buildings,
        shared_state_records=shared_state_records or [],
        scout_findings_by_building=scout_findings_by_building,
        epicenter_lat=epicenter_lat,
        epicenter_lng=epicenter_lng,
        magnitude=magnitude,
    )

    # --- Step 2-4: Grid → graph → Dijkstra ---
    candidate_path = _find_best_path(start, (target_lat, target_lng), zones)

    # --- Steps 5-7: Fetch pano IDs concurrently, annotate hazards ---
    pano_ids: list[str | None] = await asyncio.gather(
        *[streetview.get_panorama_id(lat, lng) for lat, lng in candidate_path]
    )

    waypoints: list[Waypoint] = []
    last_heading = _bearing(start_lat, start_lng, target_lat, target_lng)

    for i, ((lat, lng), pano_id) in enumerate(zip(candidate_path, pano_ids)):
        if not pano_id:
            # Skip points with no Street View coverage (same rule as Task 8 original).
            continue

        # Heading faces toward the next sample point.
        if i < len(candidate_path) - 1:
            next_lat, next_lng = candidate_path[i + 1]
            heading = _bearing(lat, lng, next_lat, next_lng)
            last_heading = heading
        else:
            heading = last_heading

        hazard = classify_waypoint_hazard(lat, lng, zones)

        waypoints.append(
            Waypoint(lat=lat, lng=lng, heading=heading, pano_id=pano_id, hazard=hazard)
        )

    return waypoints


# ---------------------------------------------------------------------------
# Path-finding orchestration (extracted for testability)
# ---------------------------------------------------------------------------

def _find_best_path(
    start: tuple[float, float],
    target: tuple[float, float],
    zones: list[HazardZone],
) -> list[tuple[float, float]]:
    """Run grid + Dijkstra; fall back to straight-line samples if no improvement.

    The straight-line path is always a valid candidate in the grid (centre
    column), so Dijkstra will only choose a detour when it actually reduces
    total cost.  If the grid is trivially small (≤ 2 nodes), return straight-
    line samples directly.

    Returns
    -------
    Ordered list of (lat, lng) waypoint coordinates.
    """
    # Always include the straight-line samples as the fallback.
    straight = _straight_line_samples(start, target)

    if not zones:
        # No hazards — straight line is optimal.
        return straight

    # Generate candidate grid.
    nodes = _generate_grid(start, target)
    if len(nodes) <= 2:
        return straight

    graph = _build_graph(nodes)

    # Dijkstra: start=0 (first node = start), end=last node (= target).
    path_indices = _dijkstra(
        nodes=nodes,
        graph=graph,
        start_idx=0,
        end_idx=len(nodes) - 1,
        zones=zones,
    )

    # Extract coordinates.
    dijkstra_path = [nodes[i] for i in path_indices]

    # Compare total hazard cost of Dijkstra path vs. straight-line path.
    # Only use Dijkstra result if it is meaningfully different (≥ 1 % cheaper).
    # This prevents micro-detours that provide negligible benefit.
    straight_cost = _path_total_cost(straight, zones)
    dijkstra_cost = _path_total_cost(dijkstra_path, zones)

    if dijkstra_cost < straight_cost * 0.99:
        return dijkstra_path
    return straight


def _path_total_cost(
    path: list[tuple[float, float]],
    zones: list[HazardZone],
) -> float:
    """Return the total Dijkstra cost for an ordered sequence of waypoints."""
    if len(path) < 2:
        return 0.0
    total = 0.0
    for i in range(len(path) - 1):
        dist = _haversine_m(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
        total += _edge_cost(path[i], path[i + 1], dist, zones)
    return total
