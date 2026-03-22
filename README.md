# Aegis-Net Backend

Earthquake incident command platform — HackIllinois 2026, HackVoyager Track.

A commander types a disaster scenario → the backend runs triage scoring on nearby buildings → deploys AI scout agents to priority buildings → scouts stream real-time facade analyses back via WebSocket.

**Person B owns this repo.** Person A (frontend) consumes the WebSocket API. Person C provides scoring/VLM/annotation/route functions that slot into the stubs here.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | `python --version` to check |
| pip | any recent | comes with Python |
| Git | any | for cloning |

You'll also need API keys (see [Environment Variables](#environment-variables) below).

---

## Setup

### 1. Clone and enter the repo

```bash
git clone <repo-url>
cd guardian
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Activate it:
# macOS / Linux
source venv/bin/activate

# Windows (PowerShell)
venv\Scripts\Activate.ps1

# Windows (cmd)
venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -r backend/requirements.txt
pip install pytest pytest-asyncio pytest-mock  # dev/test deps
```

### 4. Set environment variables

Create a `.env` file at the repo root (same level as `backend/`):

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_MAPS_API_KEY=AIza...
MAPBOX_TOKEN=pk.eyJ...
DEMO_MODE=false
FALLBACK_TO_HAIKU=true
```

Then load it before running the server:

```bash
# macOS / Linux
export $(cat .env | xargs)

# Windows PowerShell
Get-Content .env | ForEach-Object { $k,$v = $_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k,$v) }
```

> **Getting API keys:**
> - **Anthropic** — [console.anthropic.com](https://console.anthropic.com). Enable Claude API access.
> - **Google Maps** — [console.cloud.google.com](https://console.cloud.google.com). Enable **Street View Static API** and **Maps JavaScript API**. Set a billing alert at $10 — free credit covers the hackathon.
> - **Mapbox** — [account.mapbox.com](https://account.mapbox.com). Create a public token.

---

## Running the Server

```bash
# From the guardian/ directory (with venv active and env vars loaded)
uvicorn backend.main:app --reload --port 8000
```

The server starts at `http://localhost:8000`.

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Returns `{"status": "ok"}` — use this to verify the server is up |
| `WS /ws` | Single persistent WebSocket connection for all real-time traffic |

Person A's frontend connects to `ws://localhost:8000/ws`.

---

## Running Tests

```bash
# From guardian/ (no API keys needed — all external calls are mocked)
pytest

# Verbose output
pytest -v

# Single file
pytest tests/test_schemas.py

# Single test
pytest tests/test_agents_scout.py::test_scout_arrive_emits_scout_deployed
```

All 187 tests run in ~1–2 seconds. No network calls are made.

---

## Demo Mode (no Street View API calls)

Pre-cache Street View data for the VT campus demo buildings first:

```bash
GOOGLE_MAPS_API_KEY=<your-key> python -m backend.precache
```

This saves images to `backend/cache/images/` and panorama IDs to `backend/cache/panos.json`.

Then run the server in demo mode — Street View calls hit local cache, VLM calls still go live:

```bash
DEMO_MODE=true uvicorn backend.main:app --reload --port 8000
```

---

## Project Structure

```
guardian/
├── backend/
│   ├── main.py               FastAPI app — WebSocket endpoint, message dispatch
│   ├── config.py             Loads settings from environment variables
│   ├── precache.py           Pre-fetch Street View data for demo (run once)
│   ├── requirements.txt
│   ├── agents/
│   │   ├── coordinator.py    Scout fleet lifecycle + message routing  [Task 6]
│   │   ├── scout.py          Single-scout execution loop              [Task 5 ✓]
│   │   └── state.py          Cross-reference shared state             [Task 7]
│   ├── models/
│   │   └── schemas.py        All Pydantic models — WebSocket contract
│   └── services/
│       ├── osm.py            OpenStreetMap building data              [Task 2 ✓]
│       ├── streetview.py     Google Street View API + viewpoints      [Task 3 ✓]
│       ├── triage.py         Triage scoring pipeline                  [Task 4]
│       ├── vlm.py            Claude Sonnet vision analysis            [Task 5 ✓]
│       ├── annotation.py     Image annotation stub → Person C         [Task 5 ✓]
│       └── route.py          Route calculation stub → Person C        [Task 8]
├── tests/
│   ├── conftest.py           Shared fixtures (cache resets, test data)
│   ├── test_config.py
│   ├── test_schemas.py       Schema contract tests (Tasks 4/7/8 must not break these)
│   ├── test_services_osm.py
│   ├── test_services_streetview.py
│   ├── test_services_vlm.py
│   ├── test_services_annotation.py
│   ├── test_agents_scout.py
│   └── test_websocket.py
├── CLAUDE.md                 Claude Code guide for this repo
├── Architecture.md           Full system architecture reference
├── pyproject.toml            pytest configuration
└── README.md
```

---

## WebSocket Quick Reference

Every message is JSON with a top-level `"type"` field.

### Frontend → Backend

```jsonc
// Start a scenario (triggers triage + auto-deploy)
{"type": "start_scenario", "prompt": "M6.5 earthquake near VT campus", "center": {"lat": 37.2284, "lng": -80.4234}, "radius_m": 1000}

// Ask a scout a question
{"type": "commander_message", "scout_id": "alpha", "message": "What's the structural risk on the north side?"}

// Manually deploy a scout to a building
{"type": "deploy_scout", "building_id": "<osm_id>"}

// Request a route walkthrough
{"type": "request_route", "building_id": "<osm_id>", "start": {"lat": 37.229, "lng": -80.421}}
```

### Backend → Frontend

```jsonc
// Triage complete
{"type": "triage_result", "scenario_id": "...", "buildings": [...]}

// Scout deployed to a building
{"type": "scout_deployed", "scout_id": "alpha", "building_id": "...", "building_name": "...", "status": "arriving"}

// Scout analysis of a viewpoint (main chat message)
{"type": "scout_report", "scout_id": "alpha", "building_id": "...", "viewpoint": {...}, "analysis": {...}, "annotated_image_b64": "...", "narrative": "..."}

// One scout's finding affects another building
{"type": "cross_reference", "from_scout": "alpha", "to_scout": "bravo", "finding": "...", "impact": "...", "resolution": null}

// Route walkthrough waypoints
{"type": "route_result", "target_building_id": "...", "waypoints": [...]}

// Any error (socket stays open)
{"type": "error", "message": "..."}
```

Full schema definitions: [`backend/models/schemas.py`](backend/models/schemas.py)

---

## Person C Integration Points

Person C provides four functions. Drop-in replacements for these stubs:

| Stub file | Function | Replace with |
|-----------|----------|-------------|
| `backend/services/triage.py` | `score_buildings(buildings, magnitude, epicenter_lat, epicenter_lng, time_of_day)` | Real scoring algorithm |
| `backend/services/annotation.py` | `annotate_image(image_bytes, findings) -> bytes` | Pillow bounding-box drawing |
| `backend/services/route.py` | `calculate_route(start, target_building, hazard_buildings) -> list[Waypoint]` | A* pathfinding |
| `backend/services/vlm.py` | Already calls Claude — Person C may tune the prompt in `build_system_prompt()` | — |

Signatures must not change — the test suite will catch any mismatch.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'backend'`**
Run `uvicorn` and `pytest` from the `guardian/` directory, not from inside `backend/`.

**`422 Unprocessable Entity` on WebSocket messages**
The message is missing a required field. Check the schema in `backend/models/schemas.py` or the WebSocket Quick Reference above.

**Street View returns grey placeholder images**
The coordinates have no Street View coverage. Use the precache script with known VT campus coordinates, or switch to `DEMO_MODE=true`.

**`anthropic.AuthenticationError`**
`ANTHROPIC_API_KEY` is not set or is invalid. Run `echo $ANTHROPIC_API_KEY` to verify it's in your shell environment.

**Tests fail with `ImportError`**
Make sure the venv is active (`which python` should point inside `venv/`) and deps are installed (`pip install -r backend/requirements.txt`).
