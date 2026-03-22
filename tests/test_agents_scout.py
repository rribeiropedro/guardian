"""Tests for backend/agents/scout.py — Scout lifecycle with all I/O mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.schemas import (
    ScoutAnalysis,
    ScoutViewpoint,
    VLMAnalysis,
    Finding,
    ExternalRisk,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def four_viewpoints() -> list[ScoutViewpoint]:
    facings = ["N", "E", "S", "W"]
    headings = [0.0, 90.0, 180.0, 270.0]
    return [
        ScoutViewpoint(lat=40.712 + i * 0.001, lng=-74.006 + i * 0.001,
                       heading=headings[i], pitch=0.0, facing=facings[i])
        for i in range(4)
    ]


@pytest.fixture()
def canonical_vlm_result() -> VLMAnalysis:
    return VLMAnalysis(
        findings=[Finding(category="structural", description="Parapet crack", severity="CRITICAL")],
        risk_level="CRITICAL",
        recommended_action="Do not enter. Stage at 50m.",
        approach_viable=False,
        external_risks=[ExternalRisk(direction="N", type="debris", estimated_range_m=40.0)],
    )


@pytest.fixture()
def mock_emit() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def make_scout(scored_building, four_viewpoints, fake_image_bytes, canonical_vlm_result, mock_emit):
    """Return a Scout with all external services patched."""
    from backend.agents.scout import Scout

    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=four_viewpoints), \
         patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        scout = Scout(
            scout_id="alpha",
            building=scored_building,
            epicenter_lat=37.22,
            epicenter_lng=-80.43,
            magnitude=6.2,
            emit=mock_emit,
        )
        yield scout, mock_emit


# ---------------------------------------------------------------------------
# arrive()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scout_arrive_emits_scout_deployed(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, mock_emit = make_scout
    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=four_viewpoints), \
         patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.arrive()

    first_call_arg = mock_emit.call_args_list[0][0][0]
    assert first_call_arg["type"] == "scout_deployed"
    assert first_call_arg["status"] == "arriving"
    assert first_call_arg["scout_id"] == "alpha"


@pytest.mark.asyncio
async def test_scout_arrive_emits_scout_report_second(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, mock_emit = make_scout
    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=four_viewpoints), \
         patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.arrive()

    second_call_arg = mock_emit.call_args_list[1][0][0]
    assert second_call_arg["type"] == "scout_report"


@pytest.mark.asyncio
async def test_scout_arrive_uses_first_viewpoint(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    fetch_mock = AsyncMock(return_value=fake_image_bytes)
    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=four_viewpoints), \
         patch("backend.agents.scout.streetview.fetch_street_view_image", new=fetch_mock), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.arrive()

    call_kwargs = fetch_mock.call_args
    assert call_kwargs[0][0] == four_viewpoints[0].lat
    assert call_kwargs[0][1] == four_viewpoints[0].lng


@pytest.mark.asyncio
async def test_scout_arrive_no_viewpoints_returns_early(scored_building, mock_emit):
    from backend.agents.scout import Scout
    scout = Scout(
        scout_id="alpha", building=scored_building,
        epicenter_lat=40.71, epicenter_lng=-74.01, magnitude=6.0, emit=mock_emit,
    )
    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=[]):
        await scout.arrive()
    mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# analyze_viewpoint()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_viewpoint_returns_scout_report(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    from backend.models.schemas import ScoutReport
    scout, _ = make_scout
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        report = await scout.analyze_viewpoint(four_viewpoints[0])
    assert isinstance(report, ScoutReport)
    assert report.scout_id == "alpha"
    assert report.annotated_image_b64 != ""
    assert report.narrative.startswith("[CRITICAL]")


@pytest.mark.asyncio
async def test_analyze_viewpoint_appends_to_summaries(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.analyze_viewpoint(four_viewpoints[0])
        await scout.analyze_viewpoint(four_viewpoints[1])
    assert len(scout._analysis_summaries) == 2
    assert "CRITICAL" in scout._analysis_summaries[0]


@pytest.mark.asyncio
async def test_analyze_viewpoint_summary_includes_risk_level(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.analyze_viewpoint(four_viewpoints[0])
    summary = scout._analysis_summaries[0]
    # Summary now includes facing, risk level, and action in SITREP format
    assert "N face" in summary
    assert "CRITICAL" in summary


@pytest.mark.asyncio
async def test_analyze_viewpoint_stores_image_bytes(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.analyze_viewpoint(four_viewpoints[0])
    assert scout._current_image_bytes == fake_image_bytes


# ---------------------------------------------------------------------------
# advance()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_advance_moves_to_next_viewpoint(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout.current_viewpoint_index = 0

    fetch_mock = AsyncMock(return_value=fake_image_bytes)
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=fetch_mock), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.advance()

    assert scout.current_viewpoint_index == 1
    assert fetch_mock.call_args[0][0] == four_viewpoints[1].lat


@pytest.mark.asyncio
async def test_advance_returns_none_when_exhausted(make_scout, four_viewpoints):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout.current_viewpoint_index = 3
    result = await scout.advance()
    assert result is None


@pytest.mark.asyncio
async def test_advance_emits_scout_report(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, mock_emit = make_scout
    scout.viewpoints = four_viewpoints
    scout.current_viewpoint_index = 0
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.advance()
    assert mock_emit.call_args[0][0]["type"] == "scout_report"


# ---------------------------------------------------------------------------
# handle_question()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_question_appends_to_summaries(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = fake_image_bytes
    with patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("What do you see?")
    assert len(scout._analysis_summaries) == 1
    assert "follow-up" in scout._analysis_summaries[0]


@pytest.mark.asyncio
async def test_handle_question_reuses_cached_image(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = fake_image_bytes
    fetch_mock = AsyncMock(return_value=fake_image_bytes)
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=fetch_mock), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Any structural damage?")
    fetch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_question_fetches_if_no_cache(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = None
    fetch_mock = AsyncMock(return_value=fake_image_bytes)
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=fetch_mock), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Any structural damage?")
    fetch_mock.assert_called_once()


@pytest.mark.asyncio
async def test_handle_question_directional_advances_to_south(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints  # [N, E, S, W]
    scout.current_viewpoint_index = 0
    fetch_mock = AsyncMock(return_value=fake_image_bytes)
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=fetch_mock), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Check the south side.")
    assert scout.current_viewpoint_index == 2  # index of "S" viewpoint


@pytest.mark.asyncio
async def test_handle_question_no_advance_when_already_at_facing(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout.current_viewpoint_index = 2  # already at "S"
    scout._current_image_bytes = fake_image_bytes
    vlm_mock = AsyncMock(return_value=canonical_vlm_result)
    with patch("backend.agents.scout.vlm_service.analyze_image", new=vlm_mock), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("south facade?")
    # Should re-analyze, not advance — still at index 2
    assert scout.current_viewpoint_index == 2
    vlm_mock.assert_called_once()


@pytest.mark.asyncio
async def test_handle_question_emits_report(make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result):
    scout, mock_emit = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = fake_image_bytes
    with patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Status?")
    assert mock_emit.call_args[0][0]["type"] == "scout_report"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def test_detect_requested_facing_north(make_scout):
    scout, _ = make_scout
    assert scout._detect_requested_facing("What does the north side look like?") == "N"


def test_detect_requested_facing_none_on_generic(make_scout):
    scout, _ = make_scout
    assert scout._detect_requested_facing("Any structural damage visible?") is None


def test_distance_to_epicenter_positive(make_scout):
    scout, _ = make_scout
    dist = scout._distance_to_epicenter()
    assert isinstance(dist, float)
    assert dist > 0


def test_bearing_to_epicenter_in_range(make_scout):
    scout, _ = make_scout
    b = scout._bearing_to_epicenter()
    assert 0 <= b < 360


def test_epicenter_cardinal_is_valid(make_scout):
    scout, _ = make_scout
    cardinals = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert scout._epicenter_cardinal() in cardinals


def test_build_system_prompt_contains_building_name(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    assert "Test Hall" in prompt


# ---------------------------------------------------------------------------
# _build_system_prompt — building profile forwarding
# ---------------------------------------------------------------------------

def test_build_system_prompt_passes_material(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    # scored_building fixture has material="masonry"
    assert "masonry" in prompt.lower()


def test_build_system_prompt_passes_height(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    # scored_building has height_m=9.0
    assert "9m" in prompt or "9.0m" in prompt


def test_build_system_prompt_passes_triage_score(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    # scored_building has triage_score=72.0 and color="ORANGE"
    assert "72" in prompt
    assert "ORANGE" in prompt


def test_build_system_prompt_passes_damage_probability(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    # damage_probability=0.45 → "45%"
    assert "45%" in prompt


def test_build_system_prompt_includes_atc20_sections(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(four_viewpoints[0])
    assert "ATC-20" in prompt
    assert "SECTION 1" in prompt
    assert "SECTION 7" in prompt


def test_build_system_prompt_with_cross_ref_context(make_scout, four_viewpoints):
    scout, _ = make_scout
    prompt = scout._build_system_prompt(
        four_viewpoints[0],
        cross_ref_context="INTER-SECTOR HAZARD ADVISORY — gas detected.",
    )
    assert "INTER-SECTOR HAZARD ADVISORY" in prompt


# ---------------------------------------------------------------------------
# analyze_viewpoint — write_findings called with building_name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_viewpoint_writes_building_name_to_shared_state(
    make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result,
):
    from backend.agents.state import get_shared_state
    scout, _ = make_scout
    with patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=canonical_vlm_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.analyze_viewpoint(four_viewpoints[0])

    state = get_shared_state()
    # canonical_vlm_result has one external_risk → one record should be written
    assert len(state._records) == 1
    assert state._records[0].building_name == "Test Hall"


@pytest.mark.asyncio
async def test_analyze_viewpoint_does_not_write_when_no_external_risks(
    scored_building, mock_emit, four_viewpoints, fake_image_bytes,
):
    from backend.agents.scout import Scout
    from backend.agents.state import get_shared_state
    from backend.models.schemas import VLMAnalysis

    no_risk_result = VLMAnalysis(
        findings=[],
        risk_level="LOW",
        recommended_action="Clear",
        approach_viable=True,
        external_risks=[],
    )
    with patch("backend.agents.scout.streetview.calculate_viewpoints", return_value=four_viewpoints), \
         patch("backend.agents.scout.streetview.fetch_street_view_image", new=AsyncMock(return_value=fake_image_bytes)), \
         patch("backend.agents.scout.vlm_service.analyze_image", new=AsyncMock(return_value=no_risk_result)), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        scout = Scout(
            scout_id="alpha", building=scored_building,
            epicenter_lat=40.71, epicenter_lng=-74.01, magnitude=6.0, emit=mock_emit,
        )
        await scout.analyze_viewpoint(four_viewpoints[0])

    state = get_shared_state()
    assert state._records == []


# ---------------------------------------------------------------------------
# handle_question — SITREP injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_question_sitrep_injected_into_system_prompt(
    make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result,
):
    """System prompt seen by VLM should contain prior SITREP when summaries exist."""
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = fake_image_bytes
    # Pre-populate a summary so SITREP injection fires
    scout._analysis_summaries = ["[N face | CRITICAL] 1 finding(s), 1 CRITICAL | Action: Do not enter."]

    captured_prompts = []

    async def capture_vlm(image, system_prompt, user_message=None):
        captured_prompts.append(system_prompt)
        return canonical_vlm_result

    with patch("backend.agents.scout.vlm_service.analyze_image", new=capture_vlm), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Any gas leaks visible?")

    assert len(captured_prompts) == 1
    assert "RUNNING SITREP" in captured_prompts[0]
    assert "Test Hall" in captured_prompts[0]
    assert "Any gas leaks visible?" in captured_prompts[0]


@pytest.mark.asyncio
async def test_handle_question_no_sitrep_when_no_history(
    make_scout, four_viewpoints, fake_image_bytes, canonical_vlm_result,
):
    """No SITREP section injected when _analysis_summaries is empty."""
    scout, _ = make_scout
    scout.viewpoints = four_viewpoints
    scout._current_image_bytes = fake_image_bytes
    scout._analysis_summaries = []

    captured_prompts = []

    async def capture_vlm(image, system_prompt, user_message=None):
        captured_prompts.append(system_prompt)
        return canonical_vlm_result

    with patch("backend.agents.scout.vlm_service.analyze_image", new=capture_vlm), \
         patch("backend.agents.scout.annotation.annotate_image", new=AsyncMock(side_effect=lambda img, _: img)):
        await scout.handle_question("Status?")

    assert "RUNNING SITREP" not in captured_prompts[0]


# ---------------------------------------------------------------------------
# _enrich_cross_ref — ICS templates (OpenClaw disabled / fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_cross_ref_gas_returns_underground_language(make_scout):
    from types import SimpleNamespace
    scout, _ = make_scout
    record = SimpleNamespace(
        scout_id="bravo",
        building_id="bldg-2",
        building_name="Lab West",
        origin_lat=40.712,
        origin_lng=-74.006,
        risk_type="gas",
        direction="E",
        estimated_range_m=75.0,
    )
    with patch("backend.services.openclaw_client.get_openclaw_client", new=AsyncMock(return_value=None)):
        finding, impact, resolution = await scout._enrich_cross_ref(record)

    assert "underground" in finding.lower()
    assert "utility corridor" in finding.lower()
    # Template path (OpenClaw disabled) returns None for resolution
    assert resolution is None


@pytest.mark.asyncio
async def test_enrich_cross_ref_debris_returns_staging_distance_language(make_scout):
    from types import SimpleNamespace
    scout, _ = make_scout
    record = SimpleNamespace(
        scout_id="bravo",
        building_id="bldg-2",
        building_name="Torgersen Hall",
        origin_lat=40.712,
        origin_lng=-74.006,
        risk_type="debris",
        direction="N",
        estimated_range_m=40.0,
    )
    with patch("backend.services.openclaw_client.get_openclaw_client", new=AsyncMock(return_value=None)):
        finding, impact, resolution = await scout._enrich_cross_ref(record)

    assert "underground" not in finding.lower()
    assert "40" in finding  # range_m appears in finding
    assert "Torgersen Hall" in impact


@pytest.mark.asyncio
async def test_enrich_cross_ref_includes_from_building_name(make_scout):
    from types import SimpleNamespace
    scout, _ = make_scout
    record = SimpleNamespace(
        scout_id="charlie",
        building_id="bldg-3",
        building_name="Owens Hall",
        origin_lat=40.712,
        origin_lng=-74.006,
        risk_type="structural",
        direction="W",
        estimated_range_m=30.0,
    )
    with patch("backend.services.openclaw_client.get_openclaw_client", new=AsyncMock(return_value=None)):
        finding, impact, resolution = await scout._enrich_cross_ref(record)

    assert "Owens Hall" in finding
    assert "Owens Hall" in impact
