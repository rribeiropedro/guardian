# Aegis-Net — Person B (Backend & Agent Lead)

## Role
You own the entire backend: FastAPI server, WebSocket infrastructure, agent lifecycle, VLM integration, cross-reference system, and demo stability.

## Project
Earthquake incident command platform. Commander types a disaster scenario → 3D triage map → AI scout agents deploy to priority buildings → first-person route walkthroughs with hazard warnings.

---

## Running the Server

```bash
# From guardian/
uvicorn backend.main:app --reload --port 8000
```

The WebSocket endpoint is at `ws://localhost:8000/ws`. The frontend (Person A) connects once; all real-time traffic flows through this single socket.

## Environment Variables

Create `guardian/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_MAPS_API_KEY=AIza...
MAPBOX_TOKEN=pk.eyJ...
DEMO_MODE=false
FALLBACK_TO_HAIKU=true
```

`DEMO_MODE=true` — Street View calls read from `backend/cache/` instead of hitting the API. VLM calls still go live.
`FALLBACK_TO_HAIKU=true` — If Claude Sonnet latency exceeds 6 s, switch to Haiku for the remainder of the session.

## Pre-caching (run before the demo)

```bash
GOOGLE_MAPS_API_KEY=<key> python -m backend.precache
```

Saves Street View images to `backend/cache/images/` and panorama IDs to `backend/cache/panos.json`. After this, set `DEMO_MODE=true` for the demo run.

---

## Key Conventions

### Imports
All internal imports use **relative syntax** (`from .config import …`, `from ..models.schemas import …`). The app is always launched as a package (`backend.main`), never as a bare script.

### WebSocket message routing
Every message has a top-level `"type"` field. `main.py` dispatches to a handler via `DISPATCH_TABLE`. Handlers never close the socket on error — they call `_send_error()` and return.

### Logging
Log every dispatched WebSocket message as `WS recv type=<type> scout/building=<id>` — type and ID only, never the full payload.

### External API timeouts
Every `httpx` call uses `timeout=10.0`. No exceptions.

### Schemas are the contract
`backend/models/schemas.py` is the source of truth shared with Person A (frontend). **Do not rename fields or change nesting without coordinating.** Person A builds the frontend against these exact schemas.

### Person C stubs
`services/triage.py`, `services/annotation.py`, and `services/route.py` start as stubs that you own. Person C will provide the real implementations. Design the stub signatures to match Section 3 of the PRD exactly so the swap-in is a one-liner.

---

## Task Status

| Task | Description | Status |
|------|-------------|--------|
| 1 | FastAPI skeleton + WebSocket endpoint | Done |
| 2 | OSM data service | Done |
| 3 | Street View service | Done |
| 4 | Triage pipeline integration | Done |
| 5 | Single scout execution loop | Done |
| 6 | Coordinator + auto-deploy | Done |
| 7 | Cross-reference system | Done (integrated into Task 5 via SharedState) |
| 8 | Route calculation integration | TODO — cut if behind at Hour 12 |
| 9 | Error handling, caching & demo hardening | Done (base layer) |

## Critical Path
Task 5 (scout loop) unblocks everything. If the scout loop isn't working by Hour 8, cut Tasks 7 and 8 and make the single-scout conversation flawless.

---

## Interfaces with Other People

**Person A (Frontend)** — consumes your WebSocket API. Every message you emit must match the schemas in `backend/models/schemas.py` exactly.

**Person C (Data + VLM)** — provides four functions you stub and later swap in:
- `score_buildings(buildings, magnitude, epicenter_lat, epicenter_lng, time_of_day)` → `list[ScoredBuilding]`
- `analyze_image(image_bytes, context_prompt)` → structured JSON
- `annotate_image(image_bytes, findings)` → `bytes`
- `calculate_route(start, target_building, hazard_buildings)` → `list[Waypoint]`

---

## Demo Checkpoints

| Hour | Gate |
|------|------|
| 3 | Server starts. WebSocket connects. Messages flow. |
| 8 | One scout → one building → real VLM report + image. |
| 12 | Full loop: scenario → triage → 3 scouts → chat. |
| 16 | Everything stable. Route walkthrough or cut it. |
| 20 | 5 consecutive clean runs. Record backup video. |
