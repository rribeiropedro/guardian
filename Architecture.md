# Aegis-Net Backend — Architecture

## Overview

Single FastAPI application. All real-time communication with the frontend is WebSocket-based. REST is only for health checks and initial setup. Agent instances (scouts) live in memory for the duration of a scenario session.

```
Browser (Person A)
    │  ws://localhost:8000/ws  (one persistent connection)
    │
    ▼
backend/main.py  ──  ConnectionManager  ──  DISPATCH_TABLE
    │                                              │
    │                              ┌──────────────┴──────────────┐
    │                    start_scenario        commander_message
    │                    deploy_scout          request_route
    │
    ▼
backend/agents/coordinator.py
    │  auto_deploy()  ──  stagger 3s  ──  alpha / bravo / charlie
    │  route_message()
    │  manual_deploy()
    │
    ▼
backend/agents/scout.py  (one instance per building)
    │  arrive()  →  scout_deployed + first scout_report
    │  analyze_viewpoint()  →  scout_report
    │  advance()
    │  handle_question()  →  scout_report
    │
    ├── services/streetview.py   fetch_street_view_image()
    ├── services/vlm.py          analyze_image()  [Claude Sonnet vision]
    ├── services/annotation.py   annotate_image() [stub → Person C]
    └── agents/state.py          SharedState      [cross-reference store]
```

---

## Directory Structure

```
guardian/
├── CLAUDE.md
├── Architecture.md
└── backend/
    ├── main.py               FastAPI app, WebSocket endpoint, REST routes
    ├── config.py             Settings loaded from env vars
    ├── precache.py           Pre-fetch Street View data for demo
    ├── requirements.txt
    ├── cache/
    │   ├── panos.json        {lat,lng -> pano_id} index (written by precache.py)
    │   └── images/           <lat>_<lng>_<heading>.jpg files
    ├── agents/
    │   ├── coordinator.py    Scout fleet lifecycle + message routing
    │   ├── scout.py          Single-scout execution loop
    │   └── state.py          Shared in-memory state (cross-references)
    ├── models/
    │   └── schemas.py        All Pydantic models — source of truth for API contract
    └── services/
        ├── osm.py            OpenStreetMap Overpass API wrapper
        ├── streetview.py     Google Street View Static API wrapper + viewpoint calc
        ├── triage.py         Scoring pipeline (stub → Person C)
        ├── vlm.py            Claude Sonnet vision API calls
        ├── annotation.py     Image annotation (stub → Person C)
        └── route.py          Route calculation (stub → Person C)
```

---

## WebSocket Protocol

One connection per browser client. Every frame is a JSON object with a top-level `"type"` field.

### Server → Frontend

| Type | When sent | Key fields |
|------|-----------|------------|
| `triage_result` | Once, after triage completes | `scenario_id`, `buildings[]` (scored + colored) |
| `scout_deployed` | Once per scout, staggered 3–5 s | `scout_id`, `building_id`, `status` |
| `scout_report` | Every viewpoint analysis | `scout_id`, `viewpoint`, `analysis`, `annotated_image_b64`, `narrative` |
| `cross_reference` | When one scout's finding affects another | `from_scout`, `to_scout`, `finding`, `impact`, `resolution` |
| `route_result` | After route calc completes | `target_building_id`, `waypoints[]` |
| `error` | Any handler failure | `message` |

### Frontend → Server

| Type | Triggers |
|------|----------|
| `start_scenario` | Full triage + auto-deploy pipeline |
| `commander_message` | Routes question to named scout |
| `deploy_scout` | Manual deploy to a specific building |
| `request_route` | Route walkthrough calculation |

---

## Data Flow: `start_scenario` (Tasks 1–6 — Implemented)

```
start_scenario (prompt, center, radius_m)
    │
    ├─ parse prompt ──────────────── extract magnitude, time_of_day (regex)
    ├─ osm.fetch_buildings() ──────── Overpass API → list[BuildingData]
    ├─ triage.score_buildings() ───── local heuristic (→ Person C swap-in)
    │   └─ assigns triage_score (0–100) + color (RED/ORANGE/YELLOW/GREEN)
    ├─ emit triage_result ──────────── all scored buildings, sorted by score desc
    │
    └─ Coordinator.auto_deploy(top_buildings[:3])   ← Task 6
        ├─ alpha → asyncio.sleep(0s)  → Scout.arrive()
        ├─ bravo → asyncio.sleep(3s)  → Scout.arrive()
        └─ charlie → asyncio.sleep(6s) → Scout.arrive()

deploy_scout (manual, any time)
    └─ Coordinator.manual_deploy(building) → Scout.arrive()  [fire-and-forget]

commander_message
    └─ Coordinator.route_message(scout_id, message) → Scout.handle_question()
```

## Data Flow: Scout Execution Loop (Task 5 — Implemented)

```
Scout.arrive()
    ├─ streetview.calculate_viewpoints(footprint, epicenter)
    │   └─ 4–8 viewpoints, epicenter-facing first
    ├─ emit scout_deployed (status="arriving")
    ├─ analyze_viewpoint(viewpoints[0])
    ├─ emit scout_report
    └─ emit scout_deployed (status="active")

Scout.analyze_viewpoint(viewpoint)
    ├─ state.format_cross_ref_context()  ─── inject nearby findings into prompt
    ├─ streetview.fetch_street_view_image()  ─── JPEG bytes
    ├─ vlm.analyze_image(image, system_prompt)  ─── Claude Sonnet vision
    │   └─ returns findings[], risk_level, recommended_action, approach_viable
    │       + external_risks[] (written to SharedState for cross-ref detection)
    ├─ state.write_findings()  ──────────────── persists external_risks
    ├─ state.query_nearby()  ────────────────── find consumed cross-refs
    ├─ emit cross_reference  ────────────────── once per (from_scout, to_scout) pair
    ├─ annotation.annotate_image()  ─────────── stub (→ Person C)
    └─ return ScoutReport

Scout.handle_question(message)
    ├─ detect cardinal direction in message → advance() if unvisited facing
    ├─ build system_prompt + inject last 3 analysis summaries as context
    ├─ vlm.analyze_image(image, system_prompt, user_message=message)
    └─ emit scout_report

Note: conversation context is maintained as text summaries (self._analysis_summaries)
rather than raw API message history. This avoids Anthropic's alternating-role
constraint while providing meaningful context for follow-up questions.
```

---

## Key Models (`backend/models/schemas.py`)

```
BuildingData        OSM raw data (id, name, lat, lng, footprint, material, levels, height_m, building_type)
ScoredBuilding      BuildingData + triage_score, color, damage_probability, estimated_occupancy
Building            WebSocket wire format for triage_result buildings
ScoutViewpoint      lat, lng, heading, pitch, facing (cardinal)
Finding             category, description, severity, bbox
ScoutAnalysis       risk_level, findings[], recommended_action, approach_viable
ScoutReport         Full scout_report message
CrossReference      cross_reference message
Waypoint            lat, lng, heading, pano_id, hazard?
RouteResult         route_result message
```

---

## External APIs

| Service | Used for | Timeout | Demo mode |
|---------|----------|---------|-----------|
| Overpass API | Building footprints + metadata | 10 s | N/A (fast, free) |
| Google Street View Static | JPEG images at viewpoints | 10 s | Pre-cached images |
| Google Street View Metadata | Panorama IDs | 10 s | Pre-cached panos.json |
| Anthropic Claude Sonnet | VLM facade analysis | 10 s (6 s before Haiku fallback) | Always live |

---

## Error Handling & Resilience

- **WebSocket**: handler exceptions are caught, logged, and sent as `{"type":"error","message":"..."}`. The socket is never closed on error.
- **Overpass**: exponential back-off, up to 3 retries on 429/5xx. Results cached in memory keyed by `(lat, lng, radius_m)` rounded to 4 dp.
- **Street View**: 10 s timeout. In `DEMO_MODE=true`, serves from local cache; only falls back to API if the cached file is missing.
- **VLM**: 3 retries with exponential back-off on 429/500. If all retries fail, returns a fallback `ScoutReport` with `risk_level="MODERATE"` and a narrative explaining the analysis is temporarily unavailable. If Sonnet latency > 6 s and `FALLBACK_TO_HAIKU=true`, switches to Haiku for the session.
- **All external calls**: 10 s timeout.

---

## State Management

In-memory only. No persistence between server restarts.

```python
# ConnectionManager  (main.py)
_connections: dict[str, WebSocket]   # client_id → websocket

# main.py module-level state
_scenario_state: dict[str, dict]      # client_id → scenario info + buildings_by_id
_coordinators: dict[str, Coordinator] # client_id → Coordinator (owns scout registry)

# Coordinator  (agents/coordinator.py)
scouts: dict[str, Scout]              # scout_id → Scout instance
_tasks: list[asyncio.Task]            # live background tasks (self-cleaning on done)

# SharedState  (agents/state.py)  ← module-level singleton get_shared_state()
_records: list[_RiskRecord]          # all external risks written by scouts
  _RiskRecord fields: scout_id, building_id, origin_lat, origin_lng,
                      risk_type, direction, estimated_range_m

# Scout  (agents/scout.py)
_analysis_summaries: list[str]       # text summaries of completed analyses
_emitted_cross_refs: set[tuple]      # (from_scout_id, to_scout_id) already emitted

_pano_cache: dict[tuple, str]        # (lat, lng) → pano_id  (streetview.py)
_osm_cache:  dict[tuple, list]       # (lat, lng, radius_m) → buildings  (osm.py)
```

Redis upgrade path: swap `SharedState` for a Redis-backed implementation — same interface, no changes needed in `scout.py` or `coordinator.py`.

---

## Scout IDs (NATO phonetic, lowercase)

`alpha` → `bravo` → `charlie` → `delta` → `echo`

Auto-deploy always assigns alpha/bravo/charlie to the top-3 triage buildings. Manual deploys via `deploy_scout` continue the sequence.

---

## VLM System Prompt Template

```
You are analyzing the {facing} facade of {building_name}.
Epicenter is to the {epicenter_direction} (bearing {bearing}°), {distance}m away, magnitude {magnitude}.
{neighbor_context}
{cross_reference_context}

Analyze this facade for:
1. Construction type visible (masonry, steel, glass, concrete)
2. Structural vulnerability indicators (parapets, overhangs, soft stories)
3. Access points (doors, loading docks, parking approaches)
4. Overhead hazards (trees, power lines, signage, canopies)
5. Route obstructions visible

Return JSON:
{
  "findings": [...],
  "risk_level": "CRITICAL"|"MODERATE"|"LOW",
  "recommended_action": "string",
  "approach_viable": boolean,
  "external_risks": [{"direction": "string", "type": "string", "estimated_range_m": number}]
}
```

`external_risks` is used by the cross-reference system to detect when a finding at one building spatially affects a neighbor within 100 m.

---

## Triage Scoring (Stub — Person C will replace)

```
score = (100 - distance_pct) × material_weight × occupancy_estimate

material_weights:
  unreinforced_masonry=1.0  masonry=0.8  concrete=0.6
  steel=0.4  wood=0.5  unknown=0.7

occupancy (8am–6pm weekday × building type):
  lecture_hall=300  office=50  residential=10  …  else halve

color:  score≥75 → RED  |  ≥50 → ORANGE  |  ≥25 → YELLOW  |  else GREEN
```
