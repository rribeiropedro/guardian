"""Tests for backend/agents/state.py — SharedState cross-reference store."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_risk(direction="N", risk_type="debris", range_m=100.0):
    """Minimal ExternalRisk-like object accepted by write_findings."""
    from types import SimpleNamespace
    return SimpleNamespace(direction=direction, type=risk_type, estimated_range_m=range_m)


# ---------------------------------------------------------------------------
# _RiskRecord — dataclass fields
# ---------------------------------------------------------------------------

def test_risk_record_has_building_name_field():
    from backend.agents.state import _RiskRecord
    r = _RiskRecord(
        scout_id="alpha",
        building_id="bldg-1",
        building_name="North Hall",
        origin_lat=40.712,
        origin_lng=-74.006,
        risk_type="debris",
        direction="N",
        estimated_range_m=50.0,
    )
    assert r.building_name == "North Hall"


def test_risk_record_building_name_distinct_from_id():
    from backend.agents.state import _RiskRecord
    r = _RiskRecord(
        scout_id="alpha",
        building_id="bldg-1",
        building_name="West AJ Building",
        origin_lat=40.712,
        origin_lng=-74.006,
        risk_type="gas",
        direction="S",
        estimated_range_m=80.0,
    )
    assert r.building_name != r.building_id


# ---------------------------------------------------------------------------
# write_findings — stores building_name
# ---------------------------------------------------------------------------

def test_write_findings_stores_building_name():
    from backend.agents.state import SharedState
    state = SharedState()
    risk = _make_risk(direction="E", risk_type="gas", range_m=60.0)
    state.write_findings(
        scout_id="alpha",
        building_id="bldg-1",
        lat=40.712,
        lng=-74.006,
        external_risks=[risk],
        building_name="Squires Hall",
    )
    assert state._records[0].building_name == "Squires Hall"


def test_write_findings_falls_back_to_building_id_when_no_name():
    from backend.agents.state import SharedState
    state = SharedState()
    risk = _make_risk()
    state.write_findings(
        scout_id="alpha",
        building_id="bldg-2",
        lat=40.712,
        lng=-74.006,
        external_risks=[risk],
        # building_name omitted — default ""
    )
    assert state._records[0].building_name == "bldg-2"


def test_write_findings_stores_multiple_risks():
    from backend.agents.state import SharedState
    state = SharedState()
    risks = [
        _make_risk(direction="N", risk_type="gas", range_m=50.0),
        _make_risk(direction="E", risk_type="debris", range_m=30.0),
    ]
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, risks, "Hall A")
    assert len(state._records) == 2
    assert all(r.building_name == "Hall A" for r in state._records)


# ---------------------------------------------------------------------------
# reset_for_scenario — clears all records
# ---------------------------------------------------------------------------

def test_reset_for_scenario_clears_records():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk()], "Hall A")
    assert len(state._records) == 1
    state.reset_for_scenario()
    assert state._records == []


def test_reset_for_scenario_accepts_scenario_id():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk()], "Hall A")
    # Must accept scenario_id without error and still clear records
    state.reset_for_scenario(scenario_id="s-001")
    assert state._records == []


def test_reset_for_scenario_is_idempotent():
    from backend.agents.state import SharedState
    state = SharedState()
    state.reset_for_scenario()
    state.reset_for_scenario()  # second call should not raise
    assert state._records == []


# ---------------------------------------------------------------------------
# query_nearby — distance filtering
# ---------------------------------------------------------------------------

def test_query_nearby_returns_record_within_range():
    from backend.agents.state import SharedState
    state = SharedState()
    # Write a 200m gas hazard; query a point 100m away
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "Hall A")
    results = state.query_nearby(40.713, -74.006)  # ~111m north
    assert len(results) == 1


def test_query_nearby_excludes_record_beyond_range():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=50.0)], "Hall A")
    results = state.query_nearby(40.718, -74.006)  # far away
    assert results == []


def test_query_nearby_excludes_own_scout():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=500.0)], "Hall A")
    # Same scout queries nearby — should not see its own record
    results = state.query_nearby(40.712, -74.006, exclude_scout_id="alpha")
    assert results == []


def test_query_nearby_returns_other_scout_record():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=500.0)], "Hall A")
    results = state.query_nearby(40.712, -74.006, exclude_scout_id="bravo")
    assert len(results) == 1
    assert results[0].scout_id == "alpha"


# ---------------------------------------------------------------------------
# format_cross_ref_context — ICS advisory format
# ---------------------------------------------------------------------------

def test_format_cross_ref_context_empty_when_no_nearby():
    from backend.agents.state import SharedState
    state = SharedState()
    # No records → empty string
    context = state.format_cross_ref_context(40.712, -74.006)
    assert context == ""


def test_format_cross_ref_context_contains_inter_sector_header():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "Hall A")
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "INTER-SECTOR HAZARD ADVISORY" in context


def test_format_cross_ref_context_includes_building_name():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "North Hall")
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "North Hall" in context


def test_format_cross_ref_context_includes_scout_id():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "Hall A")
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "alpha" in context


def test_format_cross_ref_context_gas_gets_underground_migration_note():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings(
        "alpha", "bldg-1", 40.712, -74.006,
        [_make_risk(risk_type="gas", range_m=200.0)], "Gas Station Annex",
    )
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "underground" in context.lower()
    assert "utility corridor" in context.lower()


def test_format_cross_ref_context_chemical_gets_underground_migration_note():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings(
        "alpha", "bldg-1", 40.712, -74.006,
        [_make_risk(risk_type="chemical", range_m=200.0)], "Lab Building",
    )
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "underground" in context.lower()


def test_format_cross_ref_context_structural_debris_gets_approach_corridor_note():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings(
        "alpha", "bldg-1", 40.712, -74.006,
        [_make_risk(risk_type="debris", range_m=200.0)], "Hall A",
    )
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "corridor" in context.lower()
    # Structural debris should NOT have underground migration note
    assert "underground" not in context.lower()


def test_format_cross_ref_context_electrical_not_underground():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings(
        "alpha", "bldg-1", 40.712, -74.006,
        [_make_risk(risk_type="electrical", range_m=200.0)], "Hall A",
    )
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "underground" not in context.lower()


def test_format_cross_ref_context_includes_action_line():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "Hall A")
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "ACTION" in context


def test_format_cross_ref_context_empty_when_excluded():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=500.0)], "Hall A")
    # Exclude the only scout — should be empty
    context = state.format_cross_ref_context(40.712, -74.006, exclude_scout_id="alpha")
    assert context == ""


def test_format_cross_ref_context_multiple_records():
    from backend.agents.state import SharedState
    state = SharedState()
    state.write_findings("alpha", "bldg-1", 40.712, -74.006, [_make_risk(range_m=200.0)], "Hall A")
    state.write_findings("bravo", "bldg-2", 40.712, -74.006, [_make_risk(risk_type="gas", range_m=200.0)], "Lab B")
    context = state.format_cross_ref_context(40.713, -74.006)
    assert "alpha" in context
    assert "bravo" in context


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

def test_get_shared_state_returns_same_instance():
    from backend.agents.state import get_shared_state
    s1 = get_shared_state()
    s2 = get_shared_state()
    assert s1 is s2
