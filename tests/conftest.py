"""Shared fixtures for the Aegis-Net backend test suite."""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Cache / module-state resets (autouse — run around every test)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_lru_caches():
    from backend.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_osm_cache():
    import backend.services.osm as osm_mod
    osm_mod._cache.clear()
    yield
    osm_mod._cache.clear()


@pytest.fixture(autouse=True)
def clear_streetview_caches():
    import backend.services.streetview as sv_mod
    sv_mod._PANO_CACHE.clear()
    sv_mod._call_count = 0
    yield
    sv_mod._PANO_CACHE.clear()


@pytest.fixture(autouse=True)
def reset_haiku_mode():
    import backend.services.vlm as vlm_mod
    vlm_mod._haiku_mode = False
    yield
    vlm_mod._haiku_mode = False


@pytest.fixture(autouse=True)
def clear_shared_state():
    """Reset the module-level SharedState singleton between tests.

    Without this, risk records written by one test leak into the next,
    making cross-reference tests order-dependent.
    """
    from backend.agents.state import get_shared_state
    get_shared_state().reset_for_scenario()
    yield
    get_shared_state().reset_for_scenario()


@pytest.fixture(autouse=True)
def reset_openclaw_client():
    """Reset the OpenClaw singleton so connection state doesn't bleed between tests."""
    import backend.services.openclaw_client as oc_mod
    oc_mod._client = None
    oc_mod._init_done = False
    yield
    oc_mod._client = None
    oc_mod._init_done = False


# ---------------------------------------------------------------------------
# Common data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_footprint() -> list[list[float]]:
    """4-vertex square, ~10×10 m — triggers 4-viewpoint path."""
    return [
        [37.2283, -80.4235],
        [37.2284, -80.4235],
        [37.2284, -80.4233],
        [37.2283, -80.4233],
    ]


@pytest.fixture()
def large_footprint() -> list[list[float]]:
    """4-vertex rectangle, bounding box >400 m² — triggers 8-viewpoint path."""
    return [
        [37.220, -80.425],
        [37.222, -80.425],
        [37.222, -80.423],
        [37.220, -80.423],
    ]


@pytest.fixture()
def scored_building(minimal_footprint):
    from backend.models.schemas import ScoredBuilding
    return ScoredBuilding(
        id="bldg-1",
        name="Test Hall",
        lat=37.2284,
        lng=-80.4234,
        footprint=minimal_footprint,
        triage_score=72.0,
        color="ORANGE",
        damage_probability=0.45,
        estimated_occupancy=80,
        material="masonry",
        levels=3,
        height_m=9.0,
    )


@pytest.fixture()
def fake_image_bytes() -> bytes:
    """Minimal JPEG magic bytes for image-processing tests."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 100


@pytest.fixture()
def vlm_json_response() -> str:
    """Canonical VLMAnalysis-shaped JSON string."""
    return json.dumps({
        "findings": [
            {
                "category": "structural",
                "description": "Parapet crack",
                "severity": "CRITICAL",
                "bbox": [0.1, 0.1, 0.3, 0.3],
            },
            {
                "category": "overhead",
                "description": "Power line nearby",
                "severity": "MODERATE",
                "bbox": None,
            },
        ],
        "risk_level": "CRITICAL",
        "recommended_action": "Do not enter. Stage at 50m.",
        "approach_viable": False,
        "external_risks": [
            {"direction": "N", "type": "power_line", "estimated_range_m": 30.0}
        ],
    })


@pytest.fixture()
def demo_settings(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-gm-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-ant-key")
    monkeypatch.setenv("FALLBACK_TO_HAIKU", "true")


@pytest.fixture()
def live_settings(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-gm-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-ant-key")
    monkeypatch.setenv("FALLBACK_TO_HAIKU", "true")


# ---------------------------------------------------------------------------
# Schema contract fixtures (locked shapes for Tasks 4 / 7 / 8)
# ---------------------------------------------------------------------------

@pytest.fixture()
def canonical_triage_result_payload() -> dict:
    return {
        "type": "triage_result",
        "scenario_id": "s-001",
        "buildings": [
            {
                "id": "b1",
                "name": "Hall A",
                "lat": 37.228,
                "lng": -80.423,
                "footprint": [
                    [37.227, -80.424],
                    [37.229, -80.424],
                    [37.229, -80.422],
                    [37.227, -80.422],
                ],
                "triage_score": 80.0,
                "color": "RED",
                "damage_probability": 0.75,
                "estimated_occupancy": 200,
                "material": "masonry",
                "height_m": 12.0,
            }
        ],
    }
