"""
End-to-end conversation runner for Aegis-Net.

Connects to a running uvicorn server, runs the full agent conversation, and
logs every WebSocket message (inbound and outbound) to both the console and a
timestamped log file in scripts/logs/.

Usage:
    # 1. Start the server in one terminal:
    #    uvicorn backend.main:app --reload --port 8000

    # 2. Run this script in another:
    #    python scripts/run_conversation.py

Optional env overrides:
    SERVER_URL=ws://localhost:8000/ws  (default)
    CENTER_LAT=37.2284
    CENTER_LNG=-80.4234
    RADIUS_M=500
    SCENARIO_PROMPT="M6.5 earthquake near Blacksburg, VA at night"
    REQUEST_ROUTE=true   (send request_route after scouts deploy)
    MAX_WAIT=120         (seconds to wait for scout activity to settle)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import websockets

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVER_URL = os.getenv("SERVER_URL", "ws://localhost:8000/ws")
CENTER_LAT = float(os.getenv("CENTER_LAT", "37.2284"))
CENTER_LNG = float(os.getenv("CENTER_LNG", "-80.4234"))
RADIUS_M = float(os.getenv("RADIUS_M", "500"))
SCENARIO_PROMPT = os.getenv(
    "SCENARIO_PROMPT",
    "M6.5 earthquake near Blacksburg, VA campus at night. High-rise dormitories at risk.",
)
REQUEST_ROUTE = os.getenv("REQUEST_ROUTE", "true").lower() in ("1", "true", "yes")
MAX_WAIT = float(os.getenv("MAX_WAIT", "120"))

# After triage, ask each active scout these questions in order.
# Questions follow ICS radio protocol — plain English, specific, actionable.
SCOUT_QUESTIONS = [
    # Q1: 360-degree structural size-up
    "Conduct ATC-20 structural assessment. Report: collapse type if any, "
    "shear cracking severity, soft-story indicators, and your placard recommendation — "
    "Green, Yellow, or Red. Specify any areas that are off-limits.",

    # Q2: Access, rescue priorities, victim indicators
    "Report access on all building faces. Identify the safest entry point for a rescue squad. "
    "Any victim indicators — sounds, movement, or last known occupancy at time of event? "
    "What is the minimum safe staging distance from this structure?",

    # Q3: Utility and overhead hazards with cascade risk
    "Assess all utility hazards — gas odor, downed lines, water main damage. "
    "Report any overhead falling hazards and their debris zone radius. "
    "Identify any hazards from this building that could affect adjacent rescue sectors. "
    "What is your external risk projection toward neighboring structures?",

    # Q4: Priority and Go/No-Go decision
    "Based on your full assessment, what is your Go/No-Go recommendation for rescue team entry? "
    "Which approach route and entry point do you recommend? "
    "How does the damage here compare to adjacent buildings — should Incident Command "
    "prioritize this structure or redirect assets?",
]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = LOG_DIR / f"conversation_{run_id}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
log = logging.getLogger("aegis.e2e")


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

_TYPE_COLORS = {
    "triage_result":   "\033[94m",   # blue
    "scout_deployed":  "\033[96m",   # cyan
    "scout_report":    "\033[92m",   # green
    "cross_reference": "\033[93m",   # yellow
    "route_result":    "\033[95m",   # magenta
    "error":           "\033[91m",   # red
}
_RESET = "\033[0m"


def _summarise(msg: dict) -> str:
    t = msg.get("type", "?")
    if t == "triage_result":
        return f"triage_result  scenario={msg.get('scenario_id','?')[:8]}… buildings={len(msg.get('buildings', []))}"
    if t == "scout_deployed":
        return f"scout_deployed  scout={msg.get('scout_id')} building={msg.get('building_id')} status={msg.get('status')}"
    if t == "scout_report":
        analysis = msg.get("analysis", {})
        findings_n = len(analysis.get("findings", []))
        return (
            f"scout_report   scout={msg.get('scout_id')} building={msg.get('building_id')} "
            f"risk={analysis.get('risk_level')} findings={findings_n} "
            f"facing={msg.get('viewpoint', {}).get('facing')}"
        )
    if t == "cross_reference":
        return (
            f"cross_reference from={msg.get('from_scout')} to={msg.get('to_scout')} "
            f"finding={str(msg.get('finding',''))[:60]}"
        )
    if t == "route_result":
        return f"route_result   target={msg.get('target_building_id')} waypoints={len(msg.get('waypoints', []))}"
    if t == "error":
        return f"ERROR          {msg.get('message','')}"
    return json.dumps(msg)[:120]


def _log_recv(msg: dict, elapsed: float) -> None:
    t = msg.get("type", "?")
    color = _TYPE_COLORS.get(t, "")
    summary = _summarise(msg)
    log.info(f"RECV  {color}[{t:20s}]{_RESET}  +{elapsed:.2f}s  {summary}")
    log.debug("      full payload: %s", json.dumps(msg, default=str))


def _log_send(msg: dict) -> None:
    log.info(f"SEND  [{msg.get('type','?'):20s}]  {json.dumps(msg)[:120]}")


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

class ConversationState:
    def __init__(self) -> None:
        self.scenario_id: str | None = None
        self.buildings: list[dict] = []
        self.active_scouts: dict[str, str] = {}   # scout_id → building_id
        self.questions_asked: dict[str, int] = {}  # scout_id → index
        self.route_requested: bool = False
        self.start_time: float = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time


# ---------------------------------------------------------------------------
# Main conversation loop
# ---------------------------------------------------------------------------

async def run() -> None:
    log.info("=" * 70)
    log.info("Aegis-Net E2E Conversation Runner")
    log.info("Log file: %s", log_file)
    log.info("Server:   %s", SERVER_URL)
    log.info("Scenario: %s", SCENARIO_PROMPT)
    log.info("=" * 70)

    try:
        async with websockets.connect(SERVER_URL, open_timeout=10) as ws:
            log.info("WebSocket connected.")
            state = ConversationState()

            # ---- 1. start_scenario ----------------------------------------
            start_msg = {
                "type": "start_scenario",
                "prompt": SCENARIO_PROMPT,
                "center": {"lat": CENTER_LAT, "lng": CENTER_LNG},
                "radius_m": RADIUS_M,
            }
            _log_send(start_msg)
            await ws.send(json.dumps(start_msg))

            # ---- 2. Message receive loop -----------------------------------
            deadline = time.monotonic() + MAX_WAIT
            scouts_fully_active: set[str] = set()

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    # No message for 5 s — check if we're done.
                    if state.active_scouts and not REQUEST_ROUTE:
                        log.info("No messages for 5 s and route not requested — finishing.")
                        break
                    if state.route_requested:
                        log.info("Route was requested; no more messages after 5 s — finishing.")
                        break
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Non-JSON frame: %r", raw[:200])
                    continue

                _log_recv(msg, state.elapsed)
                msg_type = msg.get("type")

                # ---- Handle triage_result ----------------------------------
                if msg_type == "triage_result":
                    state.scenario_id = msg.get("scenario_id")
                    state.buildings = msg.get("buildings", [])
                    log.info(
                        "Scenario started. id=%s  buildings=%d",
                        state.scenario_id, len(state.buildings),
                    )
                    for b in state.buildings:
                        log.info("  building id=%-20s name=%-30s color=%s score=%.1f",
                                 b["id"], b["name"], b["color"], b["triage_score"])

                # ---- Handle scout_deployed ---------------------------------
                elif msg_type == "scout_deployed":
                    scout_id = msg["scout_id"]
                    status = msg["status"]
                    if status == "active":
                        state.active_scouts[scout_id] = msg["building_id"]
                        scouts_fully_active.add(scout_id)
                        log.info("Scout %s is ACTIVE at building %s", scout_id, msg["building_id"])

                        # Ask first question immediately.
                        state.questions_asked[scout_id] = 0
                        q = SCOUT_QUESTIONS[0]
                        q_msg = {"type": "commander_message", "scout_id": scout_id, "message": q}
                        _log_send(q_msg)
                        await ws.send(json.dumps(q_msg))

                # ---- Handle scout_report (follow-up questions) ------------
                elif msg_type == "scout_report":
                    scout_id = msg["scout_id"]
                    if scout_id in state.questions_asked:
                        next_idx = state.questions_asked[scout_id] + 1
                        state.questions_asked[scout_id] = next_idx
                        if next_idx < len(SCOUT_QUESTIONS):
                            q = SCOUT_QUESTIONS[next_idx]
                            q_msg = {"type": "commander_message", "scout_id": scout_id, "message": q}
                            _log_send(q_msg)
                            await ws.send(json.dumps(q_msg))
                        else:
                            log.info("All questions asked to scout %s.", scout_id)

                    # Once all active scouts have answered all questions, request route.
                    # Guard: scouts_fully_active must be non-empty so we don't fire
                    # on vacuous all() truth before any scout has been registered.
                    all_done = bool(scouts_fully_active) and all(
                        state.questions_asked.get(sid, 0) >= len(SCOUT_QUESTIONS)
                        for sid in scouts_fully_active
                    )
                    if all_done and REQUEST_ROUTE and not state.route_requested and state.buildings:
                        target = state.buildings[0]  # highest-priority building
                        route_msg = {
                            "type": "request_route",
                            "building_id": target["id"],
                        }
                        _log_send(route_msg)
                        await ws.send(json.dumps(route_msg))
                        state.route_requested = True

                # ---- Handle route_result -----------------------------------
                elif msg_type == "route_result":
                    wps = msg.get("waypoints", [])
                    log.info("Route result: %d waypoints for building %s",
                             len(wps), msg.get("target_building_id"))
                    for i, wp in enumerate(wps):
                        log.info(
                            "  wp[%02d] lat=%.6f lng=%.6f heading=%05.1f pano=%s hazard=%s",
                            i, wp["lat"], wp["lng"], wp["heading"],
                            wp.get("pano_id", "")[:20], wp.get("hazard"),
                        )
                    break  # route received → conversation complete

                # ---- Handle error -----------------------------------------
                elif msg_type == "error":
                    log.error("Server error: %s", msg.get("message"))

            log.info("=" * 70)
            log.info("Conversation complete. Elapsed: %.1f s", state.elapsed)
            log.info("Scouts deployed: %d", len(scouts_fully_active))
            log.info("Route requested: %s", state.route_requested)
            log.info("Full log: %s", log_file)
            log.info("=" * 70)

    except ConnectionRefusedError:
        log.error("Connection refused. Is the server running?  uvicorn backend.main:app --reload --port 8000")
        sys.exit(1)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
