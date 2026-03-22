"""Background NemoClaw route analysis agent — Tasks 8 + NemoClaw Phase E.

Purpose
-------
After `route_result` is emitted immediately with the Dijkstra-safe route and
ghost route, this agent runs in the background.  It gives a NemoClaw agent
access to Python-side route tools so it can:

  1. Walk the proposed Dijkstra route segment by segment and verify each hop.
  2. Call `evaluate_waypoint_safety` to spot waypoints still inside hazard zones.
  3. Call `suggest_detour` around any remaining danger points.
  4. Call `compare_routes` to confirm the refined path beats the original.
  5. Emit an updated `route_result` with `agent_validated=True` and any refined
     waypoints — the frontend replaces its current route with the improved one.

This is NOT part of the commander chat.  The agent's internal tool calls and
reasoning are never emitted as WebSocket messages.  Only the final `route_result`
update is emitted, and only when the agent produces a meaningfully better path.

Tool contract
-------------
The tools are pure Python callables with JSON-serialisable args and returns.
`NemoClawClient.call_agent_with_tools()` hands these to the NemoClaw SDK
agent loop; the agent can call any tool in any order and any number of times
(subject to MAX_TOOL_TURNS budget cap).

If NemoClaw is disabled or the gateway is unreachable the function degrades
gracefully: it simply does not emit the refinement update.  The frontend
continues using the Dijkstra route, which is already hazard-aware.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..models.schemas import RouteResult, ScoredBuilding, Waypoint
from ..services.route import (
    HAZARD_BETA,
    WALK_SPEED_MPS,
    _bearing,
    _centroid,
    _find_best_path,
    _haversine_m,
    _path_total_cost,
    _straight_line_samples,
)
from ..services.route_hazards import (
    HazardZone,
    build_hazard_zones,
    classify_waypoint_hazard,
    waypoint_cost,
)
from ..services import streetview

logger = logging.getLogger(__name__)

# Maximum number of agentic tool-call turns before giving up.
MAX_TOOL_TURNS = 12
# Minimum improvement ratio to emit a refined route (avoids trivial updates).
MIN_IMPROVEMENT_RATIO = 0.05  # 5% cheaper → worth re-emitting

# Coordination defaults for shared-boundary overlap behavior.
_COORD_SECTOR_COUNT = 3
_COORD_OVERLAP_BAND_M = 25
_COORD_BOUNDARY_CHECKPOINTS = 2


# ---------------------------------------------------------------------------
# Agent context — all data the tools need, built once and passed through
# ---------------------------------------------------------------------------

@dataclass
class RouteAgentContext:
    """Everything the route agent tools need to answer questions about the route.

    Populated by run_background_route_analysis() from _handle_request_route's
    scenario state before the background task is spawned.
    """
    start: tuple[float, float]
    target_building: ScoredBuilding
    hazard_buildings: list[ScoredBuilding]
    current_waypoints: list[Waypoint]       # Dijkstra route from calculate_route()
    ghost_waypoints: list[Waypoint]         # Straight-line ghost from calculate_ghost_route()
    zones: list[HazardZone]                 # Pre-built hazard zones (avoid recomputing)
    epicenter_lat: float
    epicenter_lng: float
    magnitude: float
    scenario_prompt: str = ""
    # Map building_id → ScoredBuilding for O(1) lookup inside tools.
    _building_map: dict[str, ScoredBuilding] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._building_map = {b.id: b for b in self.hazard_buildings}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

class RouteAgentTools:
    """Python-side tool implementations callable by the NemoClaw route agent.

    Each method receives a dict of JSON-deserialized arguments and returns a
    JSON-serializable dict.  NemoClawClient.call_agent_with_tools() dispatches
    by tool name to the matching method.

    Tools are designed to answer questions a route planner would ask:
      - Is this specific point safe to stand at?
      - Which buildings are dangerous along this segment?
      - Is route A safer than route B, and by how much?
      - How do I get around this specific obstruction?
    """

    def __init__(self, ctx: RouteAgentContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # Tool: evaluate_waypoint_safety
    # ------------------------------------------------------------------

    def evaluate_waypoint_safety(self, args: dict) -> dict:
        """Evaluate the hazard cost at a geographic coordinate.

        Args (JSON):
            lat (float): Latitude.
            lng (float): Longitude.

        Returns (JSON):
            hazard_cost (float): 0 = fully safe; higher = more dangerous.
                math.inf replaced with 9999 for JSON compatibility.
            is_blocked (bool): True if inside a hard-block radius.
            nearest_hazard_label (str | null): Label of the closest active zone.
            nearest_hazard_m (float): Distance to nearest zone centre in metres.
            safe_to_approach (bool): False if hazard_cost > 2.0 (rough threshold).
        """
        lat = float(args["lat"])
        lng = float(args["lng"])

        cost = waypoint_cost(lat, lng, self._ctx.zones)
        is_blocked = math.isinf(cost)

        nearest_label: str | None = None
        nearest_dist = math.inf
        for zone in self._ctx.zones:
            d = _haversine_m(lat, lng, zone.center_lat, zone.center_lng)
            if d < nearest_dist:
                nearest_dist = d
                nearest_label = zone.label

        return {
            "hazard_cost": 9999.0 if math.isinf(cost) else round(cost, 3),
            "is_blocked": is_blocked,
            "nearest_hazard_label": nearest_label,
            "nearest_hazard_m": round(nearest_dist, 1),
            "safe_to_approach": (not is_blocked) and cost < 2.0,
        }

    # ------------------------------------------------------------------
    # Tool: get_segment_hazards
    # ------------------------------------------------------------------

    def get_segment_hazards(self, args: dict) -> dict:
        """List all hazardous buildings whose zones overlap a route segment.

        Samples the segment at 20 m intervals and returns all buildings
        whose hazard zones contain at least one sample point.

        Args (JSON):
            from_lat, from_lng (float): Segment start.
            to_lat, to_lng (float): Segment end.
            sample_interval_m (float, optional): Default 20.

        Returns (JSON):
            hazards (list): Each entry has:
                building_name, color, triage_score, damage_probability,
                distance_to_segment_m, debris_radius_m, hazard_type.
            worst_cost (float): Peak hazard cost along the segment.
            segment_safe (bool): True if worst_cost < 1.0.
        """
        from_lat = float(args["from_lat"])
        from_lng = float(args["from_lng"])
        to_lat = float(args["to_lat"])
        to_lng = float(args["to_lng"])
        interval = float(args.get("sample_interval_m", 20.0))

        total = _haversine_m(from_lat, from_lng, to_lat, to_lng)
        if total < 1.0:
            return {"hazards": [], "worst_cost": 0.0, "segment_safe": True}

        bear = _bearing(from_lat, from_lng, to_lat, to_lng)
        n = max(1, int(total / interval))

        worst_cost = 0.0
        hit_zones: set[str] = set()

        for i in range(n + 1):
            lat = from_lat + (to_lat - from_lat) * (i / n)
            lng = from_lng + (to_lng - from_lng) * (i / n)
            cost = waypoint_cost(lat, lng, self._ctx.zones)
            if math.isinf(cost):
                cost = 9999.0
            worst_cost = max(worst_cost, cost)

            for zone in self._ctx.zones:
                d = _haversine_m(lat, lng, zone.center_lat, zone.center_lng)
                if d < zone.radius_m:
                    hit_zones.add(zone.label)

        hazard_list = []
        for zone in self._ctx.zones:
            if zone.label in hit_zones and zone.source == "building":
                b = next(
                    (bld for bld in self._ctx.hazard_buildings
                     if _haversine_m(bld.lat, bld.lng, zone.center_lat, zone.center_lng) < 1.0),
                    None,
                )
                d_to_seg = min(
                    _haversine_m(zone.center_lat, zone.center_lng, from_lat, from_lng),
                    _haversine_m(zone.center_lat, zone.center_lng, to_lat, to_lng),
                )
                hazard_list.append({
                    "building_name": b.name if b else zone.label,
                    "color": b.color if b else "UNKNOWN",
                    "triage_score": round(b.triage_score, 1) if b else 0,
                    "damage_probability": round(b.damage_probability, 3) if b else 1.0,
                    "distance_to_segment_m": round(d_to_seg, 1),
                    "debris_radius_m": round(zone.radius_m, 1),
                    "hazard_type": zone.hazard_type,
                })

        return {
            "hazards": hazard_list,
            "worst_cost": round(worst_cost, 3),
            "segment_safe": worst_cost < 1.0,
        }

    # ------------------------------------------------------------------
    # Tool: compare_routes
    # ------------------------------------------------------------------

    def compare_routes(self, args: dict) -> dict:
        """Compare two route options by total hazard-weighted cost.

        Args (JSON):
            route_a (list[{lat, lng}]): First candidate path.
            route_b (list[{lat, lng}]): Second candidate path.

        Returns (JSON):
            winner ("a" | "b" | "tie"): Which route is cheaper.
            cost_a, cost_b (float): Total Dijkstra cost for each.
            improvement_pct (float): % cheaper the winner is vs. loser.
            reasoning (str): Short text explanation.
        """
        def _parse(route: list) -> list[tuple[float, float]]:
            return [(float(p["lat"]), float(p["lng"])) for p in route]

        path_a = _parse(args["route_a"])
        path_b = _parse(args["route_b"])

        cost_a = _path_total_cost(path_a, self._ctx.zones)
        cost_b = _path_total_cost(path_b, self._ctx.zones)

        if math.isinf(cost_a):
            cost_a = 99999.0
        if math.isinf(cost_b):
            cost_b = 99999.0

        if cost_a < cost_b * 0.99:
            winner = "a"
            pct = round((cost_b - cost_a) / max(cost_b, 0.001) * 100, 1)
            reasoning = f"Route A is {pct}% cheaper (less hazard exposure)."
        elif cost_b < cost_a * 0.99:
            winner = "b"
            pct = round((cost_a - cost_b) / max(cost_a, 0.001) * 100, 1)
            reasoning = f"Route B is {pct}% cheaper (less hazard exposure)."
        else:
            winner = "tie"
            pct = 0.0
            reasoning = "Routes have equivalent hazard cost — prefer shorter distance."

        return {
            "winner": winner,
            "cost_a": round(cost_a, 3),
            "cost_b": round(cost_b, 3),
            "improvement_pct": pct,
            "reasoning": reasoning,
        }

    # ------------------------------------------------------------------
    # Tool: suggest_detour
    # ------------------------------------------------------------------

    def suggest_detour(self, args: dict) -> dict:
        """Suggest a detour that avoids a specific lat/lng obstruction.

        Runs _find_best_path with an extra synthetic hazard zone placed at
        the obstruction centre so the planner is forced to route around it.

        Args (JSON):
            from_lat, from_lng (float): Detour start.
            to_lat, to_lng (float): Detour end.
            avoid_lat, avoid_lng (float): Centre of the obstruction to avoid.
            avoid_radius_m (float): Exclusion radius for the obstruction.

        Returns (JSON):
            waypoints (list[{lat, lng}]): Detour path coordinates.
            detour_distance_m (float): Total length of the suggested detour.
            straight_line_distance_m (float): Direct distance for comparison.
            detour_overhead_pct (float): % longer than the straight line.
        """
        from_lat = float(args["from_lat"])
        from_lng = float(args["from_lng"])
        to_lat = float(args["to_lat"])
        to_lng = float(args["to_lng"])
        avoid_lat = float(args["avoid_lat"])
        avoid_lng = float(args["avoid_lng"])
        avoid_radius = float(args["avoid_radius_m"])

        # Add a synthetic hard-block zone at the obstruction.
        extra_zone = HazardZone(
            center_lat=avoid_lat,
            center_lng=avoid_lng,
            radius_m=avoid_radius,
            hard_block_radius_m=avoid_radius,
            damage_probability=1.0,
            cost_multiplier=math.inf,
            label="Agent-specified obstruction",
            hazard_type="blocked",
            source="scout_finding",
            color="#7f1d1d",
        )
        augmented_zones = self._ctx.zones + [extra_zone]

        path = _find_best_path((from_lat, from_lng), (to_lat, to_lng), augmented_zones)

        detour_dist = sum(
            _haversine_m(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
            for i in range(len(path) - 1)
        )
        straight_dist = _haversine_m(from_lat, from_lng, to_lat, to_lng)
        overhead = round((detour_dist - straight_dist) / max(straight_dist, 1.0) * 100, 1)

        return {
            "waypoints": [{"lat": p[0], "lng": p[1]} for p in path],
            "detour_distance_m": round(detour_dist, 1),
            "straight_line_distance_m": round(straight_dist, 1),
            "detour_overhead_pct": overhead,
        }

    # ------------------------------------------------------------------
    # Tool: get_route_summary
    # ------------------------------------------------------------------

    def get_route_summary(self, args: dict) -> dict:
        """Summarise the current proposed safe route for the agent to review.

        No args required — returns a structured overview of the Dijkstra route.

        Returns (JSON):
            total_distance_m (float)
            waypoint_count (int)
            dangerous_waypoints (list): Waypoints with non-null hazard labels.
            max_hazard_cost (float): Worst single-waypoint cost on the route.
            ghost_route_cost (float): Total cost of the straight-line alternative.
            safe_route_cost (float): Total cost of the current proposed safe route.
            ghost_danger_zones (int): Number of ghost waypoints inside a hazard zone.
        """
        safe_coords = [(w.lat, w.lng) for w in self._ctx.current_waypoints]
        ghost_coords = [(w.lat, w.lng) for w in self._ctx.ghost_waypoints]

        safe_cost = _path_total_cost(safe_coords, self._ctx.zones) if safe_coords else 0.0
        ghost_cost = _path_total_cost(ghost_coords, self._ctx.zones) if ghost_coords else 0.0

        dangerous = []
        max_cost = 0.0
        for w in self._ctx.current_waypoints:
            c = waypoint_cost(w.lat, w.lng, self._ctx.zones)
            if math.isinf(c):
                c = 9999.0
            max_cost = max(max_cost, c)
            if w.hazard is not None:
                dangerous.append({
                    "lat": w.lat, "lng": w.lng,
                    "hazard_label": w.hazard.label,
                    "hazard_type": w.hazard.type,
                    "cost": round(c, 3),
                })

        ghost_danger_count = sum(
            1 for w in self._ctx.ghost_waypoints
            if w.hazard is not None
        )

        total_dist = sum(
            _haversine_m(safe_coords[i][0], safe_coords[i][1],
                         safe_coords[i + 1][0], safe_coords[i + 1][1])
            for i in range(len(safe_coords) - 1)
        ) if len(safe_coords) > 1 else 0.0

        return {
            "total_distance_m": round(total_dist, 1),
            "waypoint_count": len(self._ctx.current_waypoints),
            "dangerous_waypoints": dangerous,
            "max_hazard_cost": round(max_cost, 3),
            "safe_route_cost": round(safe_cost if not math.isinf(safe_cost) else 99999.0, 3),
            "ghost_route_cost": round(ghost_cost if not math.isinf(ghost_cost) else 99999.0, 3),
            "ghost_danger_zones": ghost_danger_count,
        }

    # ------------------------------------------------------------------
    # Tool: get_ghost_route_analysis
    # ------------------------------------------------------------------

    def get_ghost_route_analysis(self, args: dict) -> dict:
        """Analyse the ghost (direct/GPS) route waypoint-by-waypoint.

        The ghost route is the straight-line path a normal navigation app would
        suggest — computed WITHOUT hazard avoidance.  Calling this tool reveals
        exactly which segments are dangerous and by how much, letting you justify
        why the Dijkstra safe route is the better choice.

        No args required.

        Returns (JSON):
            ghost_safe (bool): True if no ghost waypoints are inside a hazard zone.
            hazardous_waypoints (list): Waypoints with cost > 0.5, each with:
                index, lat, lng, hazard_type, hazard_label, cost.
            total_ghost_cost (float): Summed hazard cost for the ghost route.
            total_safe_cost (float): Summed hazard cost for the Dijkstra safe route.
            cost_reduction_pct (float): % improvement of safe route vs ghost.
            summary (str): One-sentence verdict comparing the two routes.
        """
        ghost_coords = [(w.lat, w.lng) for w in self._ctx.ghost_waypoints]
        safe_coords = [(w.lat, w.lng) for w in self._ctx.current_waypoints]

        ghost_cost = _path_total_cost(ghost_coords, self._ctx.zones) if ghost_coords else 0.0
        safe_cost = _path_total_cost(safe_coords, self._ctx.zones) if safe_coords else 0.0

        if math.isinf(ghost_cost):
            ghost_cost = 99999.0
        if math.isinf(safe_cost):
            safe_cost = 99999.0

        hazardous = []
        for i, w in enumerate(self._ctx.ghost_waypoints):
            c = waypoint_cost(w.lat, w.lng, self._ctx.zones)
            if math.isinf(c):
                c = 9999.0
            if c > 0.5:
                hz = classify_waypoint_hazard(w.lat, w.lng, self._ctx.zones)
                hazardous.append({
                    "index": i,
                    "lat": w.lat,
                    "lng": w.lng,
                    "hazard_type": hz.type if hz else "unknown",
                    "hazard_label": hz.label if hz else "hazard zone",
                    "cost": round(c, 3),
                })

        cost_reduction = (
            (ghost_cost - safe_cost) / max(ghost_cost, 0.001) * 100
            if ghost_cost > 0 else 0.0
        )

        if hazardous:
            summary = (
                f"Ghost route passes through {len(hazardous)} hazardous waypoint(s) "
                f"(total cost {ghost_cost:.1f}); safe Dijkstra route reduces hazard exposure "
                f"by {cost_reduction:.0f}% (total cost {safe_cost:.1f})."
            )
        else:
            summary = (
                "Ghost route has no detected hazard zones — both routes are equivalent in safety. "
                "Prefer the safe route for its explicit hazard classifications."
            )

        return {
            "ghost_safe": len(hazardous) == 0,
            "hazardous_waypoints": hazardous,
            "total_ghost_cost": round(ghost_cost, 3),
            "total_safe_cost": round(safe_cost, 3),
            "cost_reduction_pct": round(cost_reduction, 1),
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def dispatch(self, tool_name: str, args: dict) -> dict:
        """Route a tool call from the NemoClaw agent to the correct method."""
        handlers = {
            "evaluate_waypoint_safety": self.evaluate_waypoint_safety,
            "get_segment_hazards": self.get_segment_hazards,
            "compare_routes": self.compare_routes,
            "suggest_detour": self.suggest_detour,
            "get_route_summary": self.get_route_summary,
            "get_ghost_route_analysis": self.get_ghost_route_analysis,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return handler(args)
        except Exception as exc:
            logger.warning("Route agent tool %s failed: %s", tool_name, exc)
            return {"error": str(exc)}


# Compact JSON schemas handed to the NemoClaw SDK so the agent knows what
# each tool accepts. Format follows the Anthropic tool-use schema convention.
ROUTE_AGENT_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "get_route_summary",
        "description": (
            "Get a structured overview of the current proposed safe route and ghost route. "
            "Call this first to understand the route before making any changes."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "evaluate_waypoint_safety",
        "description": (
            "Evaluate the hazard cost at a specific lat/lng. "
            "Returns whether the point is inside a debris zone, the nearest hazard, "
            "and whether it is safe to approach."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lng": {"type": "number"},
            },
            "required": ["lat", "lng"],
        },
    },
    {
        "name": "get_segment_hazards",
        "description": (
            "List all hazardous buildings whose debris zones overlap a route segment. "
            "Use this to audit a specific hop in the proposed route."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_lat": {"type": "number"},
                "from_lng": {"type": "number"},
                "to_lat": {"type": "number"},
                "to_lng": {"type": "number"},
                "sample_interval_m": {"type": "number"},
            },
            "required": ["from_lat", "from_lng", "to_lat", "to_lng"],
        },
    },
    {
        "name": "compare_routes",
        "description": (
            "Compare two route options by total hazard-weighted cost. "
            "Returns which is safer and by what percentage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "route_a": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}}},
                },
                "route_b": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}}},
                },
            },
            "required": ["route_a", "route_b"],
        },
    },
    {
        "name": "suggest_detour",
        "description": (
            "Suggest a detour path that avoids a specific obstruction. "
            "Use this when a segment has a critical hazard that the current route still clips."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_lat": {"type": "number"},
                "from_lng": {"type": "number"},
                "to_lat": {"type": "number"},
                "to_lng": {"type": "number"},
                "avoid_lat": {"type": "number"},
                "avoid_lng": {"type": "number"},
                "avoid_radius_m": {"type": "number"},
            },
            "required": [
                "from_lat", "from_lng", "to_lat", "to_lng",
                "avoid_lat", "avoid_lng", "avoid_radius_m",
            ],
        },
    },
    {
        "name": "get_ghost_route_analysis",
        "description": (
            "Analyse the ghost (straight-line / GPS) route to understand exactly why it is dangerous. "
            "Call this FIRST — before auditing the safe route — to quantify how much worse the direct "
            "path is and which hazard zones it passes through. "
            "The result gives you the evidence needed to confirm or improve the Dijkstra safe route."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


# ---------------------------------------------------------------------------
# Background agent runner
# ---------------------------------------------------------------------------

def _build_agent_prompt(ctx: RouteAgentContext) -> str:
    """Build the full task prompt for the OpenClaw cloud aegis-route agent.

    All route context is serialized into the prompt so the cloud agent can
    reason over it without calling back to our server.
    """
    import dataclasses
    import json as _json

    target = ctx.target_building
    start_lat, start_lng = ctx.start
    target_lat, target_lng = _centroid(target.footprint)
    straight_dist = _haversine_m(start_lat, start_lng, target_lat, target_lng)

    # Serialize current waypoints (Pydantic → dict, drop pano_id to save tokens).
    safe_wps = [
        {"lat": w.lat, "lng": w.lng,
         "hazard": w.hazard.model_dump() if w.hazard else None}
        for w in ctx.current_waypoints
    ]
    ghost_wps = [
        {"lat": w.lat, "lng": w.lng,
         "hazard": w.hazard.model_dump() if w.hazard else None}
        for w in ctx.ghost_waypoints
    ]

    # Serialize hazard zones (dataclasses).
    zones_data = []
    for z in ctx.zones:
        d = dataclasses.asdict(z)
        # Replace math.inf with a large finite sentinel for JSON compatibility.
        d["cost_multiplier"] = 9999.0 if math.isinf(d.get("cost_multiplier", 0)) else d["cost_multiplier"]
        zones_data.append(d)

    context_json = _json.dumps({
        "start": {"lat": start_lat, "lng": start_lng},
        "target": {
            "name": target.name,
            "color": target.color,
            "damage_probability": round(target.damage_probability, 3),
            "lat": target_lat,
            "lng": target_lng,
        },
        "straight_line_distance_m": round(straight_dist),
        "magnitude": ctx.magnitude,
        "epicenter": {"lat": ctx.epicenter_lat, "lng": ctx.epicenter_lng},
        "safe_route_waypoints": safe_wps,
        "ghost_route_waypoints": ghost_wps,
        "hazard_zones": zones_data,
        "coordination": {
            "sector_count": _COORD_SECTOR_COUNT,
            "overlap_band_m": _COORD_OVERLAP_BAND_M,
            "boundary_checkpoints": _COORD_BOUNDARY_CHECKPOINTS,
        },
    }, indent=2)

    return (
        "You are the final step in the Aegis-Net ICS agent pipeline.\n\n"
        "All scout agents have completed their building assessments and cross-references. "
        "Their findings are baked into the hazard_zones below — every debris field, "
        "gas risk, and structural collapse zone that any scout observed is represented. "
        "Your job is to produce the definitive safe walking route that a first-responder "
        "will actually walk through on the ground.\n\n"
        f"## Route Context\n{context_json}\n\n"
        "## Mandatory reasoning steps — call tools in this order\n"
        "1. Call `get_ghost_route_analysis` first. "
        "Understand exactly which hazard zones the direct GPS path crosses and by how much. "
        "This gives you the baseline danger level you are trying to improve on.\n"
        "2. Call `get_route_summary` to review the Dijkstra safe route and see how it compares "
        "to the ghost route in total hazard cost.\n"
        "3. For any waypoint in the safe route that still has a non-null hazard entry, "
        "call `evaluate_waypoint_safety` to confirm its cost and whether it is blocked.\n"
        "4. If any segment still clips a hazard zone, call `get_segment_hazards` on that segment "
        "and use `suggest_detour` to route around it.\n"
        "5. Call `compare_routes` to verify your final path beats the Dijkstra route by ≥5%. "
        "If it does not, keep the Dijkstra route — it is already the best the algorithm found.\n\n"
        "## Ghost route reasoning requirement\n"
        "Your reasoning field MUST explain:\n"
        "  - How many hazard zones the ghost route passes through and their types.\n"
        "  - Why the safe route avoids these (specific detour direction if any).\n"
        "  - Whether you made any improvements or confirmed the route is already optimal.\n\n"
        "## Output\n"
        "Return ONLY a valid JSON object — no markdown, no commentary:\n"
        "{\n"
        '  "refined_waypoints": [{"lat": <float>, "lng": <float>}, ...],\n'
        '  "reasoning": "<ghost route had X hazards of type Y; safe route avoids them by Z; '
        'result: confirmed / improved by N%>"\n'
        "}\n\n"
        "If the original route is already optimal, return it unchanged with reasoning explaining "
        "why the ghost route is dangerous and the safe route is the correct choice."
        + (f"\n\nScenario context: {ctx.scenario_prompt}" if ctx.scenario_prompt else "")
    )


async def run_background_route_analysis(
    ctx: RouteAgentContext,
    emit: Callable[[dict], Awaitable[None]],
) -> None:
    """Background coroutine: run OpenClaw cloud route agent, emit refined route_result.

    This is fire-and-forget from _handle_request_route.  It:
      1. Connects to the OpenClaw cloud gateway (returns silently if disabled).
      2. Spawns the aegis-route sub-agent with full route context in the task.
      3. Parses the agent's refined waypoint list.
      4. Fetches pano IDs for any new waypoints.
      5. Emits an updated route_result with agent_validated=True if the refined
         route is ≥ MIN_IMPROVEMENT_RATIO better than the current route.

    Errors at any step are logged and swallowed — the Dijkstra route already
    emitted remains the live route for the frontend.
    """
    try:
        from ..services.openclaw_client import get_openclaw_client
        nc = await get_openclaw_client()
        if nc is None:
            logger.debug("RouteAgent: OpenClaw cloud disabled — skipping background analysis")
            return

        prompt = _build_agent_prompt(ctx)

        logger.info(
            "RouteAgent: starting background analysis for target=%s waypoints=%d",
            ctx.target_building.id,
            len(ctx.current_waypoints),
        )

        result = await nc.call_agent(
            agent_id="aegis-route",
            task=prompt,
        )

        if result is None:
            logger.warning("RouteAgent: agent returned None — keeping Dijkstra route")
            return

        # Parse refined waypoints from agent response.
        refined_coords = result.get("refined_waypoints")
        reasoning = result.get("reasoning", "")
        if not refined_coords or not isinstance(refined_coords, list):
            logger.info("RouteAgent: no refined waypoints in response — keeping Dijkstra route")
            return

        refined_path: list[tuple[float, float]] = [
            (float(p["lat"]), float(p["lng"])) for p in refined_coords
        ]

        # Check whether the refined path is actually better.
        current_path = [(w.lat, w.lng) for w in ctx.current_waypoints]
        current_cost = _path_total_cost(current_path, ctx.zones)
        refined_cost = _path_total_cost(refined_path, ctx.zones)

        if math.isinf(current_cost):
            current_cost = 99999.0
        if math.isinf(refined_cost):
            refined_cost = 99999.0

        improvement = (current_cost - refined_cost) / max(current_cost, 0.001)
        if improvement < MIN_IMPROVEMENT_RATIO:
            logger.info(
                "RouteAgent: refined route improvement %.1f%% below threshold — keeping Dijkstra route",
                improvement * 100,
            )
            # Still emit with agent_validated=True to signal the agent confirmed the route.
            refined_path = current_path

        # Fetch pano IDs for the refined path (may include new waypoints).
        pano_ids: list[str | None] = await asyncio.gather(
            *[streetview.get_panorama_id(lat, lng) for lat, lng in refined_path]
        )

        refined_waypoints: list[Waypoint] = []
        last_heading = _bearing(
            refined_path[0][0], refined_path[0][1],
            refined_path[-1][0], refined_path[-1][1],
        ) if len(refined_path) >= 2 else 0.0

        for i, ((lat, lng), pano_id) in enumerate(zip(refined_path, pano_ids)):
            if not pano_id:
                continue
            if i < len(refined_path) - 1:
                heading = _bearing(lat, lng, refined_path[i + 1][0], refined_path[i + 1][1])
                last_heading = heading
            else:
                heading = last_heading
            from ..services.route_hazards import classify_waypoint_hazard
            hazard = classify_waypoint_hazard(lat, lng, ctx.zones)
            refined_waypoints.append(
                Waypoint(lat=lat, lng=lng, heading=heading, pano_id=pano_id, hazard=hazard)
            )

        dropped = len(refined_path) - len(refined_waypoints)
        if dropped:
            logger.warning(
                "RouteAgent: %d/%d refined waypoints had no panorama and were dropped",
                dropped, len(refined_path),
            )
        if not refined_waypoints:
            logger.warning("RouteAgent: refined path produced no valid waypoints — keeping Dijkstra route")
            return

        updated_result = RouteResult(
            target_building_id=ctx.target_building.id,
            waypoints=refined_waypoints,
            ghost_waypoints=ctx.ghost_waypoints,
            agent_validated=True,
        )
        await emit(updated_result.model_dump())
        logger.info(
            "RouteAgent: emitted refined route — waypoints=%d improvement=%.1f%% reasoning=%r",
            len(refined_waypoints),
            improvement * 100,
            reasoning[:120],
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("RouteAgent: background analysis failed: %s", exc)
