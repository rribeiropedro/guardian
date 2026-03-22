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
    RouteResult,
    ScoredBuilding,
    StartScenario,
    TriageResult,
)
from .agents.coordinator import Coordinator
from .services.osm import fetch_buildings
from .services.triage import score_buildings

# One Coordinator per connected client — owns the scout registry and task lifecycle.
_coordinators: dict[str, Coordinator] = {}
# Scenario state keyed by client_id — populated by _run_start_scenario.
_scenario_state: dict[str, dict] = {}

# Shared client id for curl-friendly /api/dev/* routes (pairs start_scenario + deploy_scout).
HTTP_DEV_CLIENT_ID = "__http_dev__"

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
        "prompt": msg.prompt,
        "epicenter_lat": msg.center.lat,
        "epicenter_lng": msg.center.lng,
        "magnitude": magnitude,
        "time_of_day": time_of_day,
        "buildings_by_id": {b.id: b for b in scored},
        "top_buildings": [b.id for b in scored[:3]],
    }
    logger.info(
        "SCENARIO stored: id=%s magnitude=%.1f time=%s buildings=%d prompt=%r",
        scenario_id, magnitude, time_of_day, len(scored), msg.prompt[:80],
    )

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

    # Task 6: auto-deploy alpha / bravo / charlie to the top-3 triage buildings.
    scenario = _scenario_state[client_id]
    emit = lambda m: manager.send_personal_message(client_id, m)  # noqa: E731

    # Cancel any scouts left over from a previous scenario run.
    old_coord = _coordinators.get(client_id)
    if old_coord:
        old_coord.cancel_all()

    # Reset shared cross-reference state so prior scenario findings don't bleed in.
    from .agents.state import get_shared_state
    get_shared_state().reset_for_scenario(scenario.get("scenario_id"))

    coord = Coordinator(emit=emit)
    _coordinators[client_id] = coord

    top_buildings = [
        scenario["buildings_by_id"][bid]
        for bid in scenario["top_buildings"]
        if bid in scenario["buildings_by_id"]
    ]
    coord.auto_deploy(
        buildings=top_buildings,
        epicenter_lat=scenario["epicenter_lat"],
        epicenter_lng=scenario["epicenter_lng"],
        magnitude=scenario["magnitude"],
        scenario_prompt=scenario.get("prompt", ""),
    )


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
    coord = _coordinators.get(client_id)
    if coord is None or not coord.route_message(msg.scout_id, msg.message):
        await _send_error(client_id, f"No active scout with id '{msg.scout_id}'")


def _resolve_deploy_params(
    client_id: str,
    building_id: str,
    payload_prompt: str | None = None,
) -> tuple[ScoredBuilding, float, float, float, str]:
    """Return (building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt).

    Falls back to a minimal stub ScoredBuilding when no scenario is active,
    so scouts can be tested standalone without first running start_scenario.
    The prompt is taken from the payload first, then from scenario state.
    """
    scenario = _scenario_state.get(client_id, {})
    buildings_by_id: dict[str, ScoredBuilding] = scenario.get("buildings_by_id", {})
    scored = buildings_by_id.get(building_id)

    if scored is None:
        scored = ScoredBuilding(
            id=building_id,
            name=f"Building {building_id}",
            lat=37.2284,
            lng=-80.4234,
            footprint=[
                [37.2283, -80.4235], [37.2285, -80.4235],
                [37.2285, -80.4233], [37.2283, -80.4233],
            ],
            triage_score=50.0,
            color="ORANGE",
            damage_probability=0.5,
            estimated_occupancy=50,
        )

    epicenter_lat = scenario.get("epicenter_lat", 37.2284)
    epicenter_lng = scenario.get("epicenter_lng", -80.4234)
    magnitude = scenario.get("magnitude", 6.0)
    scenario_prompt = payload_prompt or scenario.get("prompt", "")
    prompt_source = "payload" if payload_prompt else ("scenario_state" if scenario.get("prompt") else "none")
    logger.info(
        "DEPLOY resolved: building=%s magnitude=%.1f prompt_source=%s prompt=%r",
        scored.id, magnitude, prompt_source, scenario_prompt[:80],
    )
    return scored, epicenter_lat, epicenter_lng, magnitude, scenario_prompt


async def _execute_scout_arrive(
    client_id: str,
    payload: dict,
    emit: Callable[[dict], Awaitable[None]],
) -> str:
    """Deploy a scout and await its arrival — used by the HTTP dev endpoint."""
    msg = DeployScout.model_validate(payload)
    scored, epicenter_lat, epicenter_lng, magnitude, scenario_prompt = _resolve_deploy_params(
        client_id, msg.building_id, msg.prompt
    )
    coord = Coordinator(emit=emit)
    return await coord.deploy_and_await(scored, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)


async def _handle_deploy_scout(client_id: str, payload: dict) -> None:
    """WebSocket handler: manual scout deploy — fire-and-forget via coordinator."""
    msg = DeployScout.model_validate(payload)
    scored, epicenter_lat, epicenter_lng, magnitude, scenario_prompt = _resolve_deploy_params(
        client_id, msg.building_id, msg.prompt
    )

    emit = lambda m: manager.send_personal_message(client_id, m)  # noqa: E731
    coord = _coordinators.get(client_id)
    if coord is None:
        coord = Coordinator(emit=emit)
        _coordinators[client_id] = coord

    coord.manual_deploy(scored, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)


async def _handle_request_route(client_id: str, payload: dict) -> None:
    from .services.route import calculate_route

    msg = RequestRoute.model_validate(payload)
    scenario = _scenario_state.get(client_id, {})
    buildings_by_id: dict[str, ScoredBuilding] = scenario.get("buildings_by_id", {})

    target = buildings_by_id.get(msg.building_id)
    if target is None:
        await _send_error(client_id, f"No building with id '{msg.building_id}' in current scenario.")
        return

    # Start position: use provided start or fall back to epicenter
    if msg.start is not None:
        start = (msg.start.lat, msg.start.lng)
    else:
        start = (scenario.get("epicenter_lat", target.lat), scenario.get("epicenter_lng", target.lng))

    # Hazard buildings: all scored buildings except the target
    hazard_buildings = [b for bid, b in buildings_by_id.items() if bid != msg.building_id]

    waypoints = await calculate_route(start, target, hazard_buildings)

    result = RouteResult(target_building_id=msg.building_id, waypoints=waypoints)
    await manager.send_personal_message(client_id, result.model_dump())


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
