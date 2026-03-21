from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

# Load .env from the project root (one level above this package) so that
# ANTHROPIC_API_KEY / GOOGLE_MAPS_API_KEY etc. are in the environment before
# any module reads them via os.getenv.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .config import get_settings
from .models.schemas import (
    Building,
    CommanderMessage,
    DeployScout,
    ErrorMessage,
    RequestRoute,
    ScoredBuilding,
    StartScenario,
    TriageResult,
)
from .services.osm import fetch_buildings
from .services.triage import score_buildings

# Scout registry: client_id -> {scout_id -> Scout}
_scouts: dict[str, dict[str, object]] = {}
# Scenario state set by start_scenario handler (Task 4 will populate fully)
_scenario_state: dict[str, dict] = {}
_scout_name_seq = ["alpha", "bravo", "charlie", "delta", "echo"]

# Shared client id for curl-friendly /api/dev/* routes (pairs start_scenario + deploy_scout).
HTTP_DEV_CLIENT_ID = "__http_dev__"


def _next_scout_id(client_id: str) -> str:
    used = len(_scouts.get(client_id, {}))
    return _scout_name_seq[used] if used < len(_scout_name_seq) else f"scout-{used}"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Aegis-Net Backend")
_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = str(uuid4())
        self._connections[client_id] = websocket
        return client_id

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)

    async def send_personal_message(self, client_id: str, message: dict) -> None:
        ws = self._connections.get(client_id)
        if ws is not None:
            await ws.send_json(message)

    async def broadcast(self, message: dict) -> None:
        stale_clients: list[str] = []
        for client_id, ws in self._connections.items():
            try:
                await ws.send_json(message)
            except Exception:  # pragma: no cover - transport exception path
                stale_clients.append(client_id)
        for client_id in stale_clients:
            self.disconnect(client_id)


manager = ConnectionManager()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Dev HTTP mirrors of WebSocket checks (curl-friendly; not the Person A contract) ---


@app.post("/api/dev/ws_invalid_frame")
async def dev_ws_invalid_frame(raw: bytes = Body(..., media_type="text/plain")) -> dict:
    """Simulate a WebSocket text frame that is not valid JSON."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ErrorMessage(message="Invalid JSON payload.").model_dump()
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return ErrorMessage(message="Invalid JSON payload.").model_dump()
    return ErrorMessage(
        message="Payload was valid JSON; send non-JSON text (e.g. plain not json) to test this path.",
    ).model_dump()


@app.post("/api/dev/ws_unknown_type")
async def dev_ws_unknown_type(payload: dict) -> dict:
    """Same as sending {\"type\": \"unknown_type_xyz\"} on /ws."""
    message_type = payload.get("type")
    if not isinstance(message_type, str):
        return ErrorMessage(message="Missing or invalid 'type' field.").model_dump()
    if message_type in DISPATCH_TABLE:
        return ErrorMessage(
            message=f"type '{message_type}' is known; use an unknown type to test this endpoint.",
        ).model_dump()
    return ErrorMessage(message=f"Unknown message type: {message_type}").model_dump()


@app.post("/api/dev/start_scenario")
async def dev_start_scenario(payload: dict) -> dict:
    """Same body as WebSocket start_scenario; returns triage_result JSON (Tasks 2+4)."""
    try:
        triage_msg = await _run_start_scenario(HTTP_DEV_CLIENT_ID, payload)
    except ValidationError as exc:
        return ErrorMessage(message=f"Validation error: {exc.errors()}").model_dump()
    except Exception as exc:  # pragma: no cover - mirrors WS handler safety
        logger.exception("dev_start_scenario failed")
        return ErrorMessage(message=f"Handler error: {exc}").model_dump()
    return triage_msg.model_dump()


@app.post("/api/dev/deploy_scout")
async def dev_deploy_scout(payload: dict) -> dict:
    """Same body as WebSocket deploy_scout; awaits arrive() and returns emitted WS-shaped messages."""
    collected: list[dict] = []

    async def emit(m: dict) -> None:
        collected.append(m)

    try:
        scout_id = await _execute_scout_arrive(HTTP_DEV_CLIENT_ID, payload, emit)
    except ValidationError as exc:
        return ErrorMessage(message=f"Validation error: {exc.errors()}").model_dump()
    except Exception as exc:  # pragma: no cover
        logger.exception("dev_deploy_scout failed")
        return ErrorMessage(message=f"Handler error: {exc}").model_dump()
    return {"scout_id": scout_id, "messages": collected}


async def _send_error(client_id: str, message: str) -> None:
    payload = ErrorMessage(message=message).model_dump()
    await manager.send_personal_message(client_id, payload)


async def _run_start_scenario(client_id: str, payload: dict) -> TriageResult:
    msg = StartScenario.model_validate(payload)
    scenario_id = str(uuid4())

    # Best-effort scenario parsing for Task 4 inputs.
    magnitude = _extract_magnitude(msg.prompt)
    time_of_day = _extract_time_of_day(msg.prompt)

    buildings = await fetch_buildings(msg.center.lat, msg.center.lng, msg.radius_m)
    scored = score_buildings(
        buildings=buildings,
        magnitude=magnitude,
        epicenter_lat=msg.center.lat,
        epicenter_lng=msg.center.lng,
        time_of_day=time_of_day,
    )

    _scenario_state[client_id] = {
        "scenario_id": scenario_id,
        "epicenter_lat": msg.center.lat,
        "epicenter_lng": msg.center.lng,
        "magnitude": magnitude,
        "time_of_day": time_of_day,
        "buildings_by_id": {b.id: b for b in scored},
        "top_buildings": [b.id for b in scored[:3]],
    }

    return TriageResult(
        scenario_id=scenario_id,
        buildings=[
            Building(
                id=b.id,
                name=b.name,
                lat=b.lat,
                lng=b.lng,
                footprint=b.footprint,
                triage_score=b.triage_score,
                color=b.color,
                damage_probability=b.damage_probability,
                estimated_occupancy=b.estimated_occupancy,
                material=b.material,
                height_m=b.height_m,
            )
            for b in scored
        ],
    )


async def _handle_start_scenario(client_id: str, payload: dict) -> None:
    triage_msg = await _run_start_scenario(client_id, payload)
    await manager.send_personal_message(client_id, triage_msg.model_dump())


def _extract_magnitude(prompt: str) -> float:
    # Support forms like "M6.4", "magnitude 6.4", or plain "6.4 magnitude".
    patterns = [
        r"\b[mM]\s*([0-9](?:\.[0-9])?)\b",
        r"\bmagnitude\s*[:=]?\s*([0-9](?:\.[0-9])?)\b",
        r"\b([0-9](?:\.[0-9])?)\s*magnitude\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if match:
            try:
                value = float(match.group(1))
                return min(max(value, 3.0), 9.0)
            except ValueError:
                continue
    return 6.0


def _extract_time_of_day(prompt: str) -> str:
    p = prompt.lower()
    if any(token in p for token in ("night", "overnight", "evening", "midnight")):
        return "night"
    return "day"


async def _handle_commander_message(client_id: str, payload: dict) -> None:
    msg = CommanderMessage.model_validate(payload)
    scout = _scouts.get(client_id, {}).get(msg.scout_id)
    if scout is None:
        await _send_error(client_id, f"No active scout with id '{msg.scout_id}'")
        return

    async def _run() -> None:
        await scout.handle_question(msg.message)  # type: ignore[attr-defined]

    task = asyncio.create_task(_run())
    task.add_done_callback(
        lambda t: logger.error("Scout %s handle_question failed: %s", msg.scout_id, t.exception())
        if t.exception() else None
    )


async def _execute_scout_arrive(
    client_id: str,
    payload: dict,
    emit: Callable[[dict], Awaitable[None]],
) -> str:
    from .agents.scout import Scout

    msg = DeployScout.model_validate(payload)

    # Retrieve scenario state set by _handle_start_scenario; fall back to a
    # minimal ScoredBuilding so Task 5 can be tested standalone.
    scenario = _scenario_state.get(client_id, {})
    buildings_by_id: dict[str, ScoredBuilding] = scenario.get("buildings_by_id", {})
    scored = buildings_by_id.get(msg.building_id)

    if scored is None:
        # Standalone test mode: construct a minimal ScoredBuilding
        scored = ScoredBuilding(
            id=msg.building_id,
            name=f"Building {msg.building_id}",
            lat=37.2284,
            lng=-80.4234,
            footprint=[[37.2283, -80.4235], [37.2285, -80.4235], [37.2285, -80.4233], [37.2283, -80.4233]],
            triage_score=50.0,
            color="ORANGE",
            damage_probability=0.5,
            estimated_occupancy=50,
        )

    epicenter_lat = scenario.get("epicenter_lat", 37.2284)
    epicenter_lng = scenario.get("epicenter_lng", -80.4234)
    magnitude = scenario.get("magnitude", 6.0)

    scout_id = _next_scout_id(client_id)

    scout = Scout(
        scout_id=scout_id,
        building=scored,
        epicenter_lat=epicenter_lat,
        epicenter_lng=epicenter_lng,
        magnitude=magnitude,
        emit=emit,
    )
    _scouts.setdefault(client_id, {})[scout_id] = scout

    await scout.arrive()
    return scout_id


async def _handle_deploy_scout(client_id: str, payload: dict) -> None:
    async def emit(m: dict) -> None:
        await manager.send_personal_message(client_id, m)

    async def _run() -> None:
        scout_id = await _execute_scout_arrive(client_id, payload, emit)

    task = asyncio.create_task(_run())
    task.add_done_callback(
        lambda t: logger.error("Scout deploy failed: %s", t.exception()) if t.exception() else None,
    )


async def _handle_request_route(client_id: str, payload: dict) -> None:
    msg = RequestRoute.model_validate(payload)
    await manager.send_personal_message(
        client_id,
        {
            "type": "ack",
            "received_type": msg.type,
            "building_id": msg.building_id,
            "message": "Route request received.",
        },
    )


DISPATCH_TABLE = {
    "start_scenario": _handle_start_scenario,
    "commander_message": _handle_commander_message,
    "deploy_scout": _handle_deploy_scout,
    "request_route": _handle_request_route,
}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    client_id = await manager.connect(websocket)
    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                await _send_error(client_id, "Invalid JSON payload.")
                continue

            message_type = payload.get("type")
            if not isinstance(message_type, str):
                await _send_error(client_id, "Missing or invalid 'type' field.")
                continue

            handler = DISPATCH_TABLE.get(message_type)
            if handler is None:
                await _send_error(client_id, f"Unknown message type: {message_type}")
                continue

            # Log every dispatched message: type + scout_id if present (not full payload)
            scout_id = payload.get("scout_id") or payload.get("building_id")
            if scout_id:
                logger.info("WS recv type=%s scout/building=%s client=%s", message_type, scout_id, client_id)
            else:
                logger.info("WS recv type=%s client=%s", message_type, client_id)

            try:
                await handler(client_id, payload)
            except ValidationError as exc:
                await _send_error(client_id, f"Validation error: {exc.errors()}")
            except Exception as exc:  # pragma: no cover - generic handler safety
                logger.exception("Handler failure for type=%s", message_type)
                await _send_error(client_id, f"Handler error: {exc}")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", client_id)
    finally:
        manager.disconnect(client_id)
