"""Tests for backend/models/schemas.py — schema validation and wire-format contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# BuildingData
# ---------------------------------------------------------------------------

def test_building_data_defaults():
    from backend.models.schemas import BuildingData
    b = BuildingData(id="x", name="X", lat=0.0, lng=0.0, footprint=[[0.0, 0.0]])
    assert b.material == "unknown"
    assert b.levels == 2
    assert b.height_m == 6.0
    assert b.building_type == "yes"


# ---------------------------------------------------------------------------
# ScoredBuilding
# ---------------------------------------------------------------------------

def test_scored_building_inherits_building_data(minimal_footprint):
    from backend.models.schemas import BuildingData, ScoredBuilding
    sb = ScoredBuilding(
        id="s1", name="S", lat=1.0, lng=1.0, footprint=minimal_footprint,
        triage_score=50.0, color="YELLOW", damage_probability=0.3, estimated_occupancy=10,
    )
    assert isinstance(sb, BuildingData)
    assert sb.material == "unknown"
    assert sb.footprint == minimal_footprint


@pytest.mark.parametrize("score", [-0.1, 100.1])
def test_scored_building_triage_score_bounds(minimal_footprint, score):
    from backend.models.schemas import ScoredBuilding
    with pytest.raises(ValidationError):
        ScoredBuilding(
            id="s1", name="S", lat=1.0, lng=1.0, footprint=minimal_footprint,
            triage_score=score, color="GREEN", damage_probability=0.1, estimated_occupancy=5,
        )


@pytest.mark.parametrize("prob", [-0.01, 1.01])
def test_scored_building_damage_probability_bounds(minimal_footprint, prob):
    from backend.models.schemas import ScoredBuilding
    with pytest.raises(ValidationError):
        ScoredBuilding(
            id="s1", name="S", lat=1.0, lng=1.0, footprint=minimal_footprint,
            triage_score=50.0, color="GREEN", damage_probability=prob, estimated_occupancy=5,
        )


@pytest.mark.parametrize("color", ["RED", "ORANGE", "YELLOW", "GREEN"])
def test_scored_building_color_valid(minimal_footprint, color):
    from backend.models.schemas import ScoredBuilding
    sb = ScoredBuilding(
        id="s1", name="S", lat=1.0, lng=1.0, footprint=minimal_footprint,
        triage_score=50.0, color=color, damage_probability=0.1, estimated_occupancy=5,
    )
    assert sb.color == color


def test_scored_building_color_invalid(minimal_footprint):
    from backend.models.schemas import ScoredBuilding
    with pytest.raises(ValidationError):
        ScoredBuilding(
            id="s1", name="S", lat=1.0, lng=1.0, footprint=minimal_footprint,
            triage_score=50.0, color="PURPLE", damage_probability=0.1, estimated_occupancy=5,
        )


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cat", ["structural", "access", "overhead", "route"])
def test_finding_category_valid(cat):
    from backend.models.schemas import Finding
    f = Finding(category=cat, description="d", severity="LOW")
    assert f.category == cat


def test_finding_category_invalid():
    from backend.models.schemas import Finding
    with pytest.raises(ValidationError):
        Finding(category="electrical", description="d", severity="LOW")


@pytest.mark.parametrize("sev", ["CRITICAL", "MODERATE", "LOW"])
def test_finding_severity_valid(sev):
    from backend.models.schemas import Finding
    f = Finding(category="structural", description="d", severity=sev)
    assert f.severity == sev


def test_finding_severity_invalid():
    from backend.models.schemas import Finding
    with pytest.raises(ValidationError):
        Finding(category="structural", description="d", severity="HIGH")


def test_finding_bbox_optional():
    from backend.models.schemas import Finding
    assert Finding(category="structural", description="d", severity="LOW").bbox is None
    f = Finding(category="structural", description="d", severity="LOW", bbox=[0.1, 0.2, 0.3, 0.4])
    assert f.bbox == [0.1, 0.2, 0.3, 0.4]


# ---------------------------------------------------------------------------
# VLMAnalysis
# ---------------------------------------------------------------------------

def test_vlm_analysis_defaults():
    from backend.models.schemas import VLMAnalysis
    v = VLMAnalysis()
    assert v.findings == []
    assert v.risk_level == "MODERATE"
    assert v.approach_viable is True
    assert v.external_risks == []


def test_vlm_analysis_risk_level_invalid():
    from backend.models.schemas import VLMAnalysis
    with pytest.raises(ValidationError):
        VLMAnalysis(risk_level="UNKNOWN")


# ---------------------------------------------------------------------------
# ScoutViewpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading", [-0.1, 360.1])
def test_scout_viewpoint_heading_bounds(heading):
    from backend.models.schemas import ScoutViewpoint
    with pytest.raises(ValidationError):
        ScoutViewpoint(lat=0.0, lng=0.0, heading=heading, pitch=0.0, facing="N")


@pytest.mark.parametrize("heading", [0.0, 180.0, 360.0])
def test_scout_viewpoint_heading_valid(heading):
    from backend.models.schemas import ScoutViewpoint
    vp = ScoutViewpoint(lat=0.0, lng=0.0, heading=heading, pitch=0.0, facing="N")
    assert vp.heading == heading


@pytest.mark.parametrize("facing", ["N", "NE", "E", "SE", "S", "SW", "W", "NW"])
def test_scout_viewpoint_facing_valid(facing):
    from backend.models.schemas import ScoutViewpoint
    vp = ScoutViewpoint(lat=0.0, lng=0.0, heading=0.0, pitch=0.0, facing=facing)
    assert vp.facing == facing


def test_scout_viewpoint_facing_invalid():
    from backend.models.schemas import ScoutViewpoint
    with pytest.raises(ValidationError):
        ScoutViewpoint(lat=0.0, lng=0.0, heading=0.0, pitch=0.0, facing="X")


# ---------------------------------------------------------------------------
# ScoutReport
# ---------------------------------------------------------------------------

def test_scout_report_type_literal(scored_building):
    from backend.models.schemas import Finding, ScoutAnalysis, ScoutReport, ScoutViewpoint
    vp = ScoutViewpoint(lat=37.2, lng=-80.4, heading=90.0, pitch=0.0, facing="E")
    analysis = ScoutAnalysis(
        risk_level="LOW", findings=[], recommended_action="OK", approach_viable=True,
    )
    report = ScoutReport(
        scout_id="alpha", building_id="b1", viewpoint=vp,
        analysis=analysis, annotated_image_b64="abc", narrative="narrative",
    )
    assert report.type == "scout_report"
    assert report.model_dump()["type"] == "scout_report"


# ---------------------------------------------------------------------------
# ScoutDeployed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["arriving", "active", "idle"])
def test_scout_deployed_status_valid(status):
    from backend.models.schemas import ScoutDeployed
    sd = ScoutDeployed(scout_id="a", building_id="b", building_name="B", status=status)
    assert sd.status == status


def test_scout_deployed_status_invalid():
    from backend.models.schemas import ScoutDeployed
    with pytest.raises(ValidationError):
        ScoutDeployed(scout_id="a", building_id="b", building_name="B", status="deployed")


# ---------------------------------------------------------------------------
# TriageResult — CONTRACT for Task 4
# ---------------------------------------------------------------------------

def test_triage_result_serialization(canonical_triage_result_payload):
    from backend.models.schemas import TriageResult
    result = TriageResult.model_validate(canonical_triage_result_payload)
    dumped = result.model_dump()
    assert set(dumped.keys()) == {"type", "scenario_id", "buildings"}
    assert dumped["type"] == "triage_result"
    assert len(dumped["buildings"]) == 1


def test_building_wire_format_fields():
    from backend.models.schemas import Building
    expected = {"id", "name", "lat", "lng", "footprint", "triage_score", "color",
                "damage_probability", "estimated_occupancy", "material", "height_m"}
    assert set(Building.model_fields.keys()) == expected


# ---------------------------------------------------------------------------
# CrossReference — CONTRACT for Task 7
# ---------------------------------------------------------------------------

def test_cross_reference_resolution_optional():
    from backend.models.schemas import CrossReference
    cr = CrossReference(from_scout="alpha", to_scout="bravo", finding="debris", impact="blocked")
    assert cr.resolution is None
    cr2 = CrossReference(
        from_scout="alpha", to_scout="bravo", finding="debris",
        impact="blocked", resolution="Rerouted",
    )
    assert cr2.resolution == "Rerouted"


# ---------------------------------------------------------------------------
# RouteResult / Waypoint / Hazard — CONTRACT for Task 8
# ---------------------------------------------------------------------------

def test_route_result_wire_format():
    from backend.models.schemas import RouteResult, Waypoint
    wp = Waypoint(lat=37.2, lng=-80.4, heading=90.0, pano_id="pano-1")
    rr = RouteResult(target_building_id="b1", waypoints=[wp])
    assert rr.model_dump()["type"] == "route_result"


def test_waypoint_hazard_optional():
    from backend.models.schemas import Hazard, Waypoint
    wp_no_hazard = Waypoint(lat=37.2, lng=-80.4, heading=90.0, pano_id="p1")
    assert wp_no_hazard.hazard is None
    h = Hazard(type="blocked", color="#FF0000", label="Road closed")
    wp_with_hazard = Waypoint(lat=37.2, lng=-80.4, heading=90.0, pano_id="p1", hazard=h)
    assert wp_with_hazard.hazard.type == "blocked"


@pytest.mark.parametrize("htype", ["blocked", "overhead", "turn", "arrival", "intel", "medical"])
def test_hazard_type_valid(htype):
    from backend.models.schemas import Hazard
    h = Hazard(type=htype, color="#fff", label="label")
    assert h.type == htype


def test_hazard_type_invalid():
    from backend.models.schemas import Hazard
    with pytest.raises(ValidationError):
        Hazard(type="unknown_hazard", color="#fff", label="label")


# ---------------------------------------------------------------------------
# Input messages
# ---------------------------------------------------------------------------

def test_start_scenario_requires_fields():
    from backend.models.schemas import StartScenario
    with pytest.raises(ValidationError):
        StartScenario(type="start_scenario")


def test_error_message_type_literal():
    from backend.models.schemas import ErrorMessage
    assert ErrorMessage(message="oops").model_dump()["type"] == "error"


def test_latlng_rejects_non_float():
    from backend.models.schemas import LatLng
    with pytest.raises(ValidationError):
        LatLng(lat="abc", lng=0)
