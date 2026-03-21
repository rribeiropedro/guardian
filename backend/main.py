from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .config import get_settings
from .models.schemas import CommanderMessage, DeployScout, ErrorMessage, RequestRoute, ScoredBuilding, StartScenario

# Scout registry: client_id -> {scout_id -> Scout}
_scouts: dict[str, dict[str, object]] = {}
# Scenario state set by start_scenario handler (Task 4 will populate fully)
_scenario_state: dict[str, dict] = {}
_scout_name_seq = ["alpha", "bravo", "charlie", "delta", "echo"]


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


async def _send_error(client_id: str, message: str) -> None:
    payload = ErrorMessage(message=message).model_dump()
    await manager.send_personal_message(client_id, payload)


async def _handle_start_scenario(client_id: str, payload: dict) -> None:
    msg = StartScenario.model_validate(payload)
    await manager.send_personal_message(
        client_id,
        {
            "type": "ack",
            "received_type": msg.type,
            "message": "Scenario accepted.",
        },
    )


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


async def _handle_deploy_scout(client_id: str, payload: dict) -> None:
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
    emit = lambda m: manager.send_personal_message(client_id, m)  # noqa: E731

    scout = Scout(
        scout_id=scout_id,
        building=scored,
        epicenter_lat=epicenter_lat,
        epicenter_lng=epicenter_lng,
        magnitude=magnitude,
        emit=emit,
    )
    _scouts.setdefault(client_id, {})[scout_id] = scout

    task = asyncio.create_task(scout.arrive())
    task.add_done_callback(
        lambda t: logger.error("Scout %s arrive() failed: %s", scout_id, t.exception())
        if t.exception() else None
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
