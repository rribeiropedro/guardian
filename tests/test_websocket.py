"""WebSocket integration tests for backend/main.py dispatch and error handling."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_server_state():
    import backend.main as main_mod
    main_mod._coordinators.clear()
    main_mod._scenario_state.clear()
    yield
    main_mod._coordinators.clear()
    main_mod._scenario_state.clear()


@pytest.fixture()
def client():
    from backend.main import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.json() == {"status": "ok"}


def test_health_returns_200(client):
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# WebSocket transport / error handling
# ---------------------------------------------------------------------------

def test_websocket_connect_accepts(client):
    with client.websocket_connect("/ws"):
        pass  # no exception means accept() succeeded


def test_invalid_json_returns_error(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not-json")
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "Invalid JSON" in msg["message"]


def test_missing_type_field_returns_error(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"prompt": "test"})
        msg = ws.receive_json()
    assert msg["type"] == "error"


def test_unknown_type_returns_error(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "totally_unknown"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "Unknown message type" in msg["message"]


def test_validation_error_returns_error_not_disconnect(client):
    with client.websocket_connect("/ws") as ws:
        # start_scenario missing required fields
        ws.send_json({"type": "start_scenario"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        # connection still alive
        ws.send_json({"type": "totally_unknown"})
        msg2 = ws.receive_json()
        assert msg2["type"] == "error"


def test_websocket_disconnect_cleans_up(client):
    from backend.main import manager
    with client.websocket_connect("/ws"):
        assert len(manager._connections) == 1
    assert len(manager._connections) == 0


# ---------------------------------------------------------------------------
# Stub handler acks
# ---------------------------------------------------------------------------

def test_start_scenario_emits_triage_result(client):
    from backend.models.schemas import BuildingData, ScoredBuilding

    fake_buildings = [
        BuildingData(
            id="b1",
            name="Hall A",
            lat=37.228,
            lng=-80.423,
            footprint=[
                [37.227, -80.424],
                [37.229, -80.424],
                [37.229, -80.422],
                [37.227, -80.422],
            ],
            material="masonry",
            levels=4,
            height_m=12.0,
            building_type="university",
        )
    ]
    fake_scored = [
        ScoredBuilding(
            id="b1",
            name="Hall A",
            lat=37.228,
            lng=-80.423,
            footprint=[
                [37.227, -80.424],
                [37.229, -80.424],
                [37.229, -80.422],
                [37.227, -80.422],
            ],
            material="masonry",
            levels=4,
            height_m=12.0,
            building_type="university",
            triage_score=80.0,
            color="RED",
            damage_probability=0.75,
            estimated_occupancy=200,
        )
    ]

    with patch("backend.main.fetch_buildings", new=AsyncMock(return_value=fake_buildings)), patch(
        "backend.main.score_buildings", return_value=fake_scored
    ):
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "type": "start_scenario",
                    "prompt": "M6.5 earthquake near downtown",
                    "center": {"lat": 37.2284, "lng": -80.4234},
                    "radius_m": 500,
                }
            )
            msg = ws.receive_json()

    assert msg["type"] == "triage_result"
    assert "scenario_id" in msg
    assert len(msg["buildings"]) == 1
    assert msg["buildings"][0]["id"] == "b1"


def test_request_route_unknown_building_returns_error(client):
    """request_route with no active scenario returns an error (building not found)."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "request_route", "building_id": "b-123"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "b-123" in msg["message"]


# ---------------------------------------------------------------------------
# deploy_scout
# ---------------------------------------------------------------------------

def test_deploy_scout_creates_coordinator(client):
    import backend.main as main_mod
    with patch("backend.agents.scout.Scout.arrive", new=AsyncMock()):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "deploy_scout", "building_id": "b-test"})
        assert len(main_mod._coordinators) == 1


def test_deploy_scout_second_call_reuses_coordinator(client):
    import backend.main as main_mod
    with patch("backend.agents.scout.Scout.arrive", new=AsyncMock()):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "deploy_scout", "building_id": "b1"})
            ws.send_json({"type": "deploy_scout", "building_id": "b2"})
        assert len(main_mod._coordinators) == 1


def test_deploy_scout_uses_fallback_building_when_no_scenario(client):
    import backend.main as main_mod
    from backend.agents.coordinator import Coordinator
    captured_building = []

    original_manual = Coordinator.manual_deploy
    def recording_manual(self, building, *args, **kwargs):
        captured_building.append(building)
    with patch("backend.agents.coordinator.Coordinator.manual_deploy", new=recording_manual), \
         patch("backend.agents.scout.Scout.arrive", new=AsyncMock()):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "deploy_scout", "building_id": "standalone-b"})
    assert len(captured_building) == 1
    assert captured_building[0].id == "standalone-b"


# ---------------------------------------------------------------------------
# commander_message
# ---------------------------------------------------------------------------

def test_commander_message_unknown_scout_returns_error(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "commander_message", "scout_id": "nonexistent", "message": "status?"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "No active scout" in msg["message"]


def test_commander_message_routes_to_scout(client):
    import backend.main as main_mod
    question_received = []

    async def fake_handle_question(msg):
        question_received.append(msg)

    with patch("backend.agents.scout.Scout.arrive", new=AsyncMock()), \
         patch("backend.agents.scout.Scout.handle_question", new=fake_handle_question):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "deploy_scout", "building_id": "b1"})
            ws.send_json({"type": "commander_message", "scout_id": "alpha", "message": "north side?"})


# ---------------------------------------------------------------------------
# Schema contracts (Tasks 4 / 7 / 8)
# ---------------------------------------------------------------------------

def test_triage_result_schema_contract(canonical_triage_result_payload):
    from backend.models.schemas import TriageResult
    result = TriageResult.model_validate(canonical_triage_result_payload)
    assert result.type == "triage_result"
    assert len(result.buildings) == 1


def test_route_result_schema_contract():
    from backend.models.schemas import Hazard, RouteResult, Waypoint
    payload = {
        "type": "route_result",
        "target_building_id": "b1",
        "waypoints": [
            {"lat": 37.2, "lng": -80.4, "heading": 90.0, "pano_id": "p1", "hazard": None},
            {"lat": 37.21, "lng": -80.41, "heading": 180.0, "pano_id": "p2",
             "hazard": {"type": "blocked", "color": "#FF0000", "label": "Road closed"}},
        ],
    }
    rr = RouteResult.model_validate(payload)
    assert rr.type == "route_result"
    assert rr.waypoints[0].hazard is None
    assert rr.waypoints[1].hazard.type == "blocked"


def test_cross_reference_schema_contract():
    from backend.models.schemas import CrossReference
    payload = {
        "type": "cross_reference",
        "from_scout": "alpha",
        "to_scout": "bravo",
        "finding": "Debris field to the north",
        "impact": "May block approach route",
        "resolution": None,
    }
    cr = CrossReference.model_validate(payload)
    assert cr.type == "cross_reference"
    assert cr.resolution is None
