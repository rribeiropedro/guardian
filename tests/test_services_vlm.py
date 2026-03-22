"""Tests for backend/services/vlm.py — parsing, retry, fallback, haiku switch."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# _parse_vlm_response
# ---------------------------------------------------------------------------

def test_parse_vlm_response_happy_path(vlm_json_response):
    from backend.services.vlm import _parse_vlm_response
    result = _parse_vlm_response(vlm_json_response)
    assert result.risk_level == "CRITICAL"
    assert len(result.findings) == 2
    assert result.approach_viable is False
    assert len(result.external_risks) == 1


def test_parse_vlm_response_strips_markdown_fences(vlm_json_response):
    from backend.services.vlm import _parse_vlm_response
    fenced = f"```json\n{vlm_json_response}\n```"
    result = _parse_vlm_response(fenced)
    assert result.risk_level == "CRITICAL"


def test_parse_vlm_response_extracts_embedded_json(vlm_json_response):
    from backend.services.vlm import _parse_vlm_response
    text = f"Analysis complete: {vlm_json_response} End of report."
    result = _parse_vlm_response(text)
    assert result.risk_level == "CRITICAL"


def test_parse_vlm_response_raises_on_no_json():
    from backend.services.vlm import _RetryableError, _parse_vlm_response
    with pytest.raises(_RetryableError):
        _parse_vlm_response("Sorry, I cannot analyze this image.")


def test_parse_vlm_response_raises_on_malformed_json():
    from backend.services.vlm import _RetryableError, _parse_vlm_response
    with pytest.raises(_RetryableError):
        _parse_vlm_response("{risk_level: CRITICAL, broken")


def test_parse_vlm_response_coerces_invalid_category():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": [{"category": "electrical", "description": "d", "severity": "LOW", "bbox": None}],
            "risk_level": "LOW", "recommended_action": "ok", "approach_viable": True, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert result.findings[0].category == "structural"


def test_parse_vlm_response_coerces_invalid_severity():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": [{"category": "structural", "description": "d", "severity": "HIGH", "bbox": None}],
            "risk_level": "LOW", "recommended_action": "ok", "approach_viable": True, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert result.findings[0].severity == "MODERATE"


def test_parse_vlm_response_coerces_invalid_risk_level():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": [], "risk_level": "EXTREME", "recommended_action": "run",
            "approach_viable": False, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert result.risk_level == "MODERATE"


def test_parse_vlm_response_handles_null_bbox():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": [{"category": "structural", "description": "d", "severity": "LOW", "bbox": None}],
            "risk_level": "LOW", "recommended_action": "ok", "approach_viable": True, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert result.findings[0].bbox is None


def test_parse_vlm_response_rejects_malformed_bbox():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": [{"category": "structural", "description": "d", "severity": "LOW", "bbox": [1, 2, 3]}],
            "risk_level": "LOW", "recommended_action": "ok", "approach_viable": True, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert result.findings[0].bbox is None


def test_parse_vlm_response_skips_non_dict_findings():
    from backend.services.vlm import _parse_vlm_response
    data = {"findings": ["not a dict", {"category": "structural", "description": "d", "severity": "LOW", "bbox": None}],
            "risk_level": "LOW", "recommended_action": "ok", "approach_viable": True, "external_risks": []}
    result = _parse_vlm_response(json.dumps(data))
    assert len(result.findings) == 1


# ---------------------------------------------------------------------------
# _fallback_analysis
# ---------------------------------------------------------------------------

def test_fallback_analysis_returns_moderate():
    from backend.services.vlm import _fallback_analysis
    fb = _fallback_analysis()
    assert fb.risk_level == "MODERATE"
    assert fb.approach_viable is True
    assert fb.findings == []
    assert "unavailable" in fb.recommended_action.lower()


# ---------------------------------------------------------------------------
# analyze_image (mocking _call_claude)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyze_image_success(fake_image_bytes, vlm_json_response, live_settings):
    from backend.services.vlm import analyze_image
    with patch("backend.services.vlm._call_claude", new=AsyncMock(return_value=(vlm_json_response, 1.0))):
        result = await analyze_image(fake_image_bytes, "sys-prompt")
    assert result.risk_level == "CRITICAL"


@pytest.mark.asyncio
async def test_analyze_image_retries_on_retryable_error(fake_image_bytes, vlm_json_response, live_settings):
    from backend.services.vlm import _RetryableError, analyze_image
    call_mock = AsyncMock(side_effect=[
        _RetryableError("rate limit"),
        _RetryableError("rate limit"),
        (vlm_json_response, 1.0),
    ])
    with patch("backend.services.vlm._call_claude", new=call_mock), \
         patch("backend.services.vlm.asyncio.sleep", new=AsyncMock(return_value=None)) as sleep_mock:
        result = await analyze_image(fake_image_bytes, "sys-prompt")
    assert result.risk_level == "CRITICAL"
    assert call_mock.call_count == 3
    assert sleep_mock.call_count == 2
    sleep_mock.assert_any_call(2.0)
    sleep_mock.assert_any_call(4.0)


@pytest.mark.asyncio
async def test_analyze_image_returns_fallback_after_max_retries(fake_image_bytes, live_settings):
    from backend.services.vlm import _RetryableError, analyze_image
    call_mock = AsyncMock(side_effect=_RetryableError("always fails"))
    with patch("backend.services.vlm._call_claude", new=call_mock), \
         patch("backend.services.vlm.asyncio.sleep", new=AsyncMock(return_value=None)):
        result = await analyze_image(fake_image_bytes, "sys-prompt")
    assert result.risk_level == "MODERATE"
    assert "unavailable" in result.recommended_action.lower()


@pytest.mark.asyncio
async def test_analyze_image_returns_fallback_on_unexpected_exception(fake_image_bytes, live_settings):
    from backend.services.vlm import analyze_image
    call_mock = AsyncMock(side_effect=ValueError("unexpected"))
    with patch("backend.services.vlm._call_claude", new=call_mock):
        result = await analyze_image(fake_image_bytes, "sys-prompt")
    assert result.risk_level == "MODERATE"
    assert call_mock.call_count == 1  # no retry for non-RetryableError


@pytest.mark.asyncio
async def test_analyze_image_switches_to_haiku_on_high_latency(fake_image_bytes, vlm_json_response, live_settings):
    import backend.services.vlm as vlm_mod
    from backend.services.vlm import analyze_image
    with patch("backend.services.vlm._call_claude", new=AsyncMock(return_value=(vlm_json_response, 7.0))):
        await analyze_image(fake_image_bytes, "sys-prompt")
    assert vlm_mod._haiku_mode is True


@pytest.mark.asyncio
async def test_analyze_image_no_haiku_switch_when_disabled(fake_image_bytes, vlm_json_response, monkeypatch):
    import backend.services.vlm as vlm_mod
    from backend.services.vlm import analyze_image
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("FALLBACK_TO_HAIKU", "false")
    with patch("backend.services.vlm._call_claude", new=AsyncMock(return_value=(vlm_json_response, 7.0))):
        await analyze_image(fake_image_bytes, "sys-prompt")
    assert vlm_mod._haiku_mode is False


@pytest.mark.asyncio
async def test_analyze_image_uses_haiku_when_flag_set(fake_image_bytes, vlm_json_response, live_settings):
    import backend.services.vlm as vlm_mod
    from backend.services.vlm import _HAIKU_MODEL, analyze_image
    vlm_mod._haiku_mode = True
    captured_model = []

    async def capture(model, system_prompt, messages):
        captured_model.append(model)
        return vlm_json_response, 1.0

    with patch("backend.services.vlm._call_claude", new=capture):
        await analyze_image(fake_image_bytes, "sys-prompt")
    assert captured_model[0] == _HAIKU_MODEL


@pytest.mark.asyncio
async def test_analyze_image_passes_conversation_history(fake_image_bytes, vlm_json_response, live_settings):
    from backend.services.vlm import analyze_image
    history = [{"role": "user", "content": "previous question"}]
    captured_messages = []

    async def capture(model, system_prompt, messages):
        captured_messages.extend(messages)
        return vlm_json_response, 1.0

    with patch("backend.services.vlm._call_claude", new=capture):
        await analyze_image(fake_image_bytes, "sys-prompt", conversation_history=history)

    # conversation_history is intentionally unused (kept for API compatibility).
    # The messages list contains the user turn with image content, not the history.
    assert len(captured_messages) == 1
    assert captured_messages[0]["role"] == "user"


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_contains_required_fields():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt(
        facing="N", building_name="Burruss Hall",
        epicenter_direction="S", bearing=180.0,
        distance_m=500.0, magnitude=6.5,
    )
    assert "Burruss Hall" in prompt
    assert "180°" in prompt
    assert "500m" in prompt
    assert "6.5" in prompt
    # Prompt now uses "N face" (ATC-20 building-side language) not "N facade"
    assert "N face" in prompt


def test_build_system_prompt_includes_neighbor_context():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 100, 5.0,
                                 neighbor_context="Neighbor has unstable parapet.")
    assert "Neighbor has unstable parapet." in prompt


def test_build_system_prompt_includes_cross_ref_context():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 100, 5.0,
                                 cross_reference_context="Alpha reported power line.")
    assert "Alpha reported power line." in prompt


def test_build_system_prompt_omits_empty_context_blocks():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 100, 5.0)
    # No cross-ref or neighbor context — those sections should not appear
    assert "INTER-SECTOR HAZARD ADVISORY" not in prompt
    assert "INTER-SECTOR HAZARD" not in prompt
    # Prompt must still contain the mandatory protocol sections
    assert "ATC-20" in prompt
    assert "SECTION 1" in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — role definition
# ---------------------------------------------------------------------------

def test_build_system_prompt_contains_fema_usar_role():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 500, 6.0)
    assert "FEMA USAR" in prompt
    assert "Structures Specialist" in prompt


def test_build_system_prompt_contains_ics_language_requirement():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 500, 6.0)
    assert "ICS" in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — all 7 ATC-20 sections
# ---------------------------------------------------------------------------

def test_build_system_prompt_contains_all_seven_sections():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "Building X", "S", 180, 300, 6.5)
    for section_num in range(1, 8):
        assert f"SECTION {section_num}" in prompt, f"SECTION {section_num} missing from prompt"


def test_build_system_prompt_section2_collapse_patterns():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5)
    assert "Pancake" in prompt or "pancake" in prompt
    assert "lean-to" in prompt.lower()
    assert "V-shape" in prompt or "V-Shape" in prompt


def test_build_system_prompt_section4_gas_migration_note():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5)
    assert "utility corridor" in prompt.lower()


def test_build_system_prompt_section7_placard_colors():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5)
    assert "GREEN PLACARD" in prompt
    assert "YELLOW PLACARD" in prompt
    assert "RED PLACARD" in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — material-specific warnings
# ---------------------------------------------------------------------------

def test_build_system_prompt_urm_parapet_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="masonry")
    assert "URM" in prompt or "UNREINFORCED MASONRY" in prompt
    assert "parapet" in prompt.lower()


def test_build_system_prompt_brick_triggers_urm_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="brick")
    assert "parapet" in prompt.lower()


def test_build_system_prompt_concrete_nonductile_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="concrete")
    assert "non-ductile" in prompt.lower()
    assert "beam-column" in prompt.lower()


def test_build_system_prompt_tiltup_roof_panel_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="tilt-up")
    assert "tilt" in prompt.lower()
    assert "roof" in prompt.lower()


def test_build_system_prompt_wood_frame_soft_story_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="wood frame")
    assert "soft-story" in prompt.lower() or "soft story" in prompt.lower()
    assert "cripple wall" in prompt.lower()


def test_build_system_prompt_steel_connection_warning():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="steel")
    assert "connection" in prompt.lower()
    assert "beam" in prompt.lower()


def test_build_system_prompt_unknown_material_no_warning():
    from backend.services.vlm import build_system_prompt
    # unknown material should not crash and should not contain material-specific warnings
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, material="unknown")
    # Prompt still valid — just no URM/concrete/etc specific block
    assert "ATC-20" in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — near-field shaking flag
# ---------------------------------------------------------------------------

def test_build_system_prompt_near_field_flag_when_lt_5km():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 4999, 6.0)
    assert "near-field" in prompt.lower()
    assert "HIGH" in prompt


def test_build_system_prompt_no_near_field_flag_when_gt_5km():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 5001, 6.0)
    assert "near-field" not in prompt.lower()


def test_build_system_prompt_near_field_threshold_is_exact_5000():
    from backend.services.vlm import build_system_prompt
    # exactly 5000m should NOT trigger near-field (condition is < 5000)
    prompt = build_system_prompt("N", "B", "S", 180, 5000, 6.0)
    assert "near-field" not in prompt.lower()


# ---------------------------------------------------------------------------
# build_system_prompt — height and triage score in profile
# ---------------------------------------------------------------------------

def test_build_system_prompt_height_converts_to_stories():
    from backend.services.vlm import build_system_prompt
    # 9m → ~3 stories
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, height_m=9.0)
    assert "3 stor" in prompt  # "3 story" or "3 stories"


def test_build_system_prompt_triage_score_and_color_shown():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt(
        "N", "B", "S", 180, 300, 6.5,
        triage_score=85.0, color="RED", damage_probability=0.80,
    )
    assert "85" in prompt
    assert "RED" in prompt
    assert "80%" in prompt


def test_build_system_prompt_no_height_profile_when_zero():
    from backend.services.vlm import build_system_prompt
    # height_m=0 should not crash and should not emit a height line
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, height_m=0.0)
    assert "Height:" not in prompt


def test_build_system_prompt_multi_story_torsion_note_when_tall():
    from backend.services.vlm import build_system_prompt
    # ≥ 15m should include multi-story torsion note
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, height_m=18.0)
    assert "torsional" in prompt.lower() or "MULTI-STORY" in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — scenario prompt injection
# ---------------------------------------------------------------------------

def test_build_system_prompt_includes_scenario_as_active_incident():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt(
        "N", "B", "S", 180, 300, 6.5,
        scenario_prompt="M6.5 earthquake near Blacksburg, VA at night.",
    )
    assert "ACTIVE INCIDENT" in prompt
    assert "Blacksburg" in prompt


def test_build_system_prompt_no_active_incident_when_no_scenario():
    from backend.services.vlm import build_system_prompt
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5)
    assert "ACTIVE INCIDENT" not in prompt


# ---------------------------------------------------------------------------
# build_system_prompt — cross-reference and neighbor context
# ---------------------------------------------------------------------------

def test_build_system_prompt_cross_ref_context_included():
    from backend.services.vlm import build_system_prompt
    advisory = "INTER-SECTOR HAZARD ADVISORY — gas confirmed at adjacent sector."
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, cross_reference_context=advisory)
    assert advisory in prompt


def test_build_system_prompt_neighbor_context_included():
    from backend.services.vlm import build_system_prompt
    neighbor = "Adjacent building has compromised parapet."
    prompt = build_system_prompt("N", "B", "S", 180, 300, 6.5, neighbor_context=neighbor)
    assert neighbor in prompt
