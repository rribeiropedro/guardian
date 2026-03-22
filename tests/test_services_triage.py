"""Tests for backend/services/triage.py — 5-factor scoring model."""
from __future__ import annotations

import math

import pytest

from backend.models.schemas import BuildingData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_building(
    bid: str = "b1",
    name: str = "Test Hall",
    lat: float = 37.228,
    lng: float = -80.423,
    material: str = "masonry",
    levels: int = 3,
    height_m: float = 9.0,
    building_type: str = "university",
    start_date: str = "1955",
) -> BuildingData:
    return BuildingData(
        id=bid, name=name, lat=lat, lng=lng,
        footprint=[[lat, lng], [lat + 0.0001, lng], [lat + 0.0001, lng + 0.0001], [lat, lng + 0.0001]],
        material=material, levels=levels, height_m=height_m,
        building_type=building_type, start_date=start_date,
    )


# ---------------------------------------------------------------------------
# _ground_motion_pga — Boore-Atkinson
# ---------------------------------------------------------------------------

def test_ground_motion_clamps_to_one_at_close_range():
    from backend.services.triage import _ground_motion_pga
    assert _ground_motion_pga(0.5, 6.5) == pytest.approx(1.0)


def test_ground_motion_decreases_with_distance():
    from backend.services.triage import _ground_motion_pga
    # M4.5: differentiates at large distances
    near = _ground_motion_pga(5.0, 4.5)
    far = _ground_motion_pga(50.0, 4.5)
    assert near > far


def test_ground_motion_higher_for_larger_magnitude():
    from backend.services.triage import _ground_motion_pga
    low_mag = _ground_motion_pga(40.0, 4.5)
    high_mag = _ground_motion_pga(40.0, 6.0)
    assert high_mag >= low_mag


def test_ground_motion_always_positive():
    from backend.services.triage import _ground_motion_pga
    assert _ground_motion_pga(200.0, 3.0) > 0.0


def test_ground_motion_never_exceeds_one():
    from backend.services.triage import _ground_motion_pga
    assert _ground_motion_pga(0.1, 9.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _material_factor
# ---------------------------------------------------------------------------

def test_material_factor_masonry_highest():
    from backend.services.triage import _material_factor
    assert _material_factor("masonry") == pytest.approx(1.0)


def test_material_factor_brick_highest():
    from backend.services.triage import _material_factor
    assert _material_factor("brick") == pytest.approx(1.0)


def test_material_factor_steel_lowest():
    from backend.services.triage import _material_factor
    assert _material_factor("steel") < _material_factor("wood")
    assert _material_factor("steel") < _material_factor("concrete")


def test_material_factor_reinforced_concrete_lower_than_plain():
    from backend.services.triage import _material_factor
    assert _material_factor("reinforced concrete") < _material_factor("concrete")


def test_material_factor_stone_between_masonry_and_wood():
    from backend.services.triage import _material_factor
    assert _material_factor("masonry") > _material_factor("stone") > _material_factor("wood")


def test_material_factor_unknown_returns_default():
    from backend.services.triage import _material_factor, _DEFAULT_MATERIAL_VULN
    assert _material_factor("unknown") == pytest.approx(_DEFAULT_MATERIAL_VULN)


def test_material_factor_case_insensitive():
    from backend.services.triage import _material_factor
    assert _material_factor("MASONRY") == _material_factor("masonry")


# ---------------------------------------------------------------------------
# _age_factor
# ---------------------------------------------------------------------------

def test_age_factor_pre1940_is_max():
    from backend.services.triage import _age_factor
    assert _age_factor("1930") == pytest.approx(1.0)


def test_age_factor_1955_is_085():
    from backend.services.triage import _age_factor
    assert _age_factor("1955") == pytest.approx(0.85)


def test_age_factor_1985_is_060():
    from backend.services.triage import _age_factor
    assert _age_factor("1985") == pytest.approx(0.60)


def test_age_factor_post1994_is_030():
    from backend.services.triage import _age_factor
    assert _age_factor("2005") == pytest.approx(0.30)


def test_age_factor_unknown_returns_default():
    from backend.services.triage import _age_factor
    assert _age_factor("") == pytest.approx(0.70)
    assert _age_factor("not-a-date") == pytest.approx(0.70)


def test_age_factor_older_more_vulnerable():
    from backend.services.triage import _age_factor
    assert _age_factor("1920") > _age_factor("1960") > _age_factor("2000")


def test_age_factor_accepts_full_date_string():
    from backend.services.triage import _age_factor
    # OSM may return "1968-01-01" — should parse year correctly
    assert _age_factor("1968-01-01") == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# _height_resonance
# ---------------------------------------------------------------------------

def test_height_resonance_low_rise_lowest():
    from backend.services.triage import _height_resonance
    assert _height_resonance(1) == pytest.approx(0.30)
    assert _height_resonance(2) == pytest.approx(0.30)


def test_height_resonance_mid_rise_worst():
    from backend.services.triage import _height_resonance
    assert _height_resonance(5) == pytest.approx(0.85)
    assert _height_resonance(7) == pytest.approx(0.85)


def test_height_resonance_high_rise_less_than_midrise():
    from backend.services.triage import _height_resonance
    assert _height_resonance(15) < _height_resonance(5)


def test_height_resonance_3_4_story_mid_range():
    from backend.services.triage import _height_resonance
    assert _height_resonance(3) == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# _occupancy_factor
# ---------------------------------------------------------------------------

def test_occupancy_factor_university_day():
    from backend.services.triage import _occupancy_factor
    assert _occupancy_factor("university", "day") == pytest.approx(1.0)


def test_occupancy_factor_office_drops_at_night():
    from backend.services.triage import _occupancy_factor
    day = _occupancy_factor("office", "day")
    night = _occupancy_factor("office", "night")
    assert day > night


def test_occupancy_factor_residential_stays_high_at_night():
    from backend.services.triage import _occupancy_factor
    day = _occupancy_factor("residential", "day")
    night = _occupancy_factor("residential", "night")
    # Residential should stay high or increase at night
    assert night >= day * 0.9


def test_occupancy_factor_dormitory_stays_high_at_night():
    from backend.services.triage import _occupancy_factor
    day = _occupancy_factor("dormitory", "day")
    night = _occupancy_factor("dormitory", "night")
    assert night >= day * 0.9


# ---------------------------------------------------------------------------
# score_buildings — end-to-end
# ---------------------------------------------------------------------------

def test_score_buildings_returns_list():
    from backend.services.triage import score_buildings
    buildings = [_make_building()]
    result = score_buildings(buildings, 6.5, 37.22, -80.43)
    assert len(result) == 1


def test_score_buildings_sorted_descending():
    from backend.services.triage import score_buildings
    # High-risk: pre-1940 masonry
    high_risk = _make_building("b1", material="masonry", start_date="1920",
                               lat=37.228, lng=-80.423)
    # Low-risk: post-1994 steel, far from epicenter
    low_risk = _make_building("b2", material="steel", start_date="2010",
                              lat=37.240, lng=-80.410)
    result = score_buildings([low_risk, high_risk], 6.5, 37.228, -80.423)
    assert result[0].id == "b1"
    assert result[1].id == "b2"


def test_score_buildings_masonry_scores_higher_than_steel():
    from backend.services.triage import score_buildings
    masonry = _make_building("b1", material="masonry", start_date="1955")
    steel = _make_building("b2", material="steel", start_date="2005")
    r_masonry, r_steel = (
        score_buildings([masonry], 6.5, 37.22, -80.43)[0],
        score_buildings([steel], 6.5, 37.22, -80.43)[0],
    )
    assert r_masonry.triage_score > r_steel.triage_score


def test_score_buildings_old_scores_higher_than_new_same_material():
    from backend.services.triage import score_buildings
    old = _make_building("b1", material="concrete", start_date="1935")
    new = _make_building("b2", material="concrete", start_date="2015")
    [r_old] = score_buildings([old], 6.5, 37.22, -80.43)
    [r_new] = score_buildings([new], 6.5, 37.22, -80.43)
    assert r_old.triage_score > r_new.triage_score


def test_score_buildings_triage_score_in_bounds():
    from backend.services.triage import score_buildings
    buildings = [
        _make_building("b1", material="masonry", start_date="1920"),
        _make_building("b2", material="steel", start_date="2020"),
        _make_building("b3", material="unknown", start_date=""),
    ]
    results = score_buildings(buildings, 6.5, 37.22, -80.43)
    for r in results:
        assert 0.0 <= r.triage_score <= 100.0


def test_score_buildings_damage_probability_in_bounds():
    from backend.services.triage import score_buildings
    buildings = [_make_building()]
    results = score_buildings(buildings, 6.5, 37.22, -80.43)
    for r in results:
        assert 0.05 <= r.damage_probability <= 0.98


def test_score_buildings_color_red_for_high_risk():
    from backend.services.triage import score_buildings
    # Pre-1940 masonry, mid-rise, university, epicenter on doorstep
    high = _make_building("b1", material="masonry", start_date="1920",
                          levels=5, building_type="university",
                          lat=37.228, lng=-80.423)
    [result] = score_buildings([high], 7.0, 37.228, -80.423)
    assert result.color in ("RED", "ORANGE")  # at minimum ORANGE


def test_score_buildings_color_green_for_low_risk():
    from backend.services.triage import score_buildings
    # Post-1994 steel warehouse, low-rise, far from epicenter
    low = _make_building("b2", material="steel", start_date="2010",
                         levels=1, building_type="warehouse",
                         lat=37.300, lng=-80.500)
    [result] = score_buildings([low], 4.5, 37.228, -80.423)
    assert result.color in ("GREEN", "YELLOW")


def test_score_buildings_preserves_start_date():
    from backend.services.triage import score_buildings
    b = _make_building(start_date="1962")
    [result] = score_buildings([b], 6.5, 37.22, -80.43)
    assert result.start_date == "1962"


def test_score_buildings_empty_input():
    from backend.services.triage import score_buildings
    assert score_buildings([], 6.5, 37.22, -80.43) == []


def test_score_buildings_estimated_occupancy_positive():
    from backend.services.triage import score_buildings
    b = _make_building()
    [result] = score_buildings([b], 6.5, 37.22, -80.43)
    assert result.estimated_occupancy >= 5


def test_score_buildings_night_lowers_office_occupancy():
    from backend.services.triage import score_buildings
    office = _make_building(building_type="office")
    [day_r] = score_buildings([office], 6.5, 37.22, -80.43, time_of_day="day")
    [night_r] = score_buildings([office], 6.5, 37.22, -80.43, time_of_day="night")
    assert day_r.estimated_occupancy > night_r.estimated_occupancy


def test_score_buildings_midrise_scores_higher_than_lowrise_same_material():
    from backend.services.triage import score_buildings
    mid = _make_building("b1", levels=5, material="concrete", start_date="1960")
    low = _make_building("b2", levels=1, material="concrete", start_date="1960")
    [r_mid] = score_buildings([mid], 6.5, 37.22, -80.43)
    [r_low] = score_buildings([low], 6.5, 37.22, -80.43)
    assert r_mid.triage_score > r_low.triage_score
