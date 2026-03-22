"""VLM service — wraps Anthropic Claude Sonnet vision API for scout facade analysis."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic

from ..config import get_settings
from ..models.schemas import ExternalRisk, Finding, VLMAnalysis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------
_SONNET_MODEL = "claude-sonnet-4-5"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_LATENCY_THRESHOLD_S = 6.0
_MAX_RETRIES = 3

# Session-level flag: set True once Sonnet exceeds the latency threshold
_haiku_mode: bool = False

# Valid category/severity values for coercing VLM output
_VALID_CATEGORIES = {"structural", "access", "overhead", "route"}
_VALID_SEVERITIES = {"CRITICAL", "MODERATE", "LOW"}
_VALID_RISK_LEVELS = {"CRITICAL", "MODERATE", "LOW"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_image(
    image_bytes: bytes,
    system_prompt: str,
    conversation_history: list[dict] | None = None,
    user_message: str | None = None,
) -> VLMAnalysis:
    """Call Claude vision API and return a structured VLMAnalysis.

    Args:
        image_bytes: Raw JPEG image data.
        system_prompt: System prompt describing the analysis task.
        conversation_history: Unused; kept for API compatibility. Pass None.
        user_message: Optional text prepended to the image in the user turn,
            used by handle_question to include the commander's question.

    Retries up to _MAX_RETRIES times on 429/500 or JSON parse failure.
    Falls back to Haiku if Sonnet latency exceeds _LATENCY_THRESHOLD_S.
    Returns a safe fallback VLMAnalysis if all retries are exhausted.
    """
    global _haiku_mode

    image_b64 = base64.standard_b64encode(image_bytes).decode()

    # Build the user message: optional text + image + JSON instruction
    user_content: list[dict[str, Any]] = []
    if user_message:
        user_content.append({"type": "text", "text": user_message})
    user_content.extend([
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_b64,
            },
        },
        {
            "type": "text",
            "text": (
                "Analyze this facade image according to the system prompt. "
                "Write your SITREP sentence first, then \"---\", then the JSON object."
            ),
        },
    ])

    messages = [{"role": "user", "content": user_content}]

    delay = 2.0
    for attempt in range(_MAX_RETRIES):
        model = _HAIKU_MODEL if _haiku_mode else _SONNET_MODEL
        try:
            raw_text, latency = await _call_claude(model, system_prompt, messages)

            # Check latency threshold on first attempt with Sonnet
            if not _haiku_mode and latency > _LATENCY_THRESHOLD_S and get_settings().fallback_to_haiku:
                logger.warning(
                    "Sonnet latency %.1fs exceeds threshold — switching to Haiku for this session",
                    latency,
                )
                _haiku_mode = True

            return _parse_vlm_response(raw_text)

        except _RetryableError as exc:
            if attempt < _MAX_RETRIES - 1:
                logger.warning("VLM retryable error (attempt %d/%d): %s. Sleeping %.1fs", attempt + 1, _MAX_RETRIES, exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
            else:
                logger.error("VLM all retries exhausted: %s", exc)

        except Exception as exc:
            logger.error("VLM unexpected error: %s", exc)
            break

    return _fallback_analysis()


async def analyze_image_stream(
    image_bytes: bytes,
    system_prompt: str,
    on_chunk: Callable[[str], Awaitable[None]],
    user_message: str | None = None,
) -> VLMAnalysis:
    """Streaming version of analyze_image.

    Calls on_chunk with each text token as it arrives, then returns the
    complete VLMAnalysis once streaming finishes.  Falls back to the
    non-streaming path on any error so callers never regress.

    Args:
        image_bytes: Raw JPEG image data.
        system_prompt: System prompt describing the analysis task.
        on_chunk: Async callback invoked with each incremental text chunk.
        user_message: Optional text prepended to the image in the user turn.
    """
    global _haiku_mode

    image_b64 = base64.standard_b64encode(image_bytes).decode()

    user_content: list[dict[str, Any]] = []
    if user_message:
        user_content.append({"type": "text", "text": user_message})
    user_content.extend([
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_b64,
            },
        },
        {
            "type": "text",
            "text": (
                "Analyze this facade image according to the system prompt. "
                "Write your SITREP sentence first, then \"---\", then the JSON object."
            ),
        },
    ])

    messages = [{"role": "user", "content": user_content}]
    model = _HAIKU_MODEL if _haiku_mode else _SONNET_MODEL

    try:
        client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
        start = time.monotonic()

        full_text = ""
        # Buffer used to detect the "---" delimiter that separates the
        # plain-English chat message from the JSON block.  Only tokens
        # before the delimiter are forwarded to on_chunk so the frontend
        # sees a human-readable message, not raw JSON.
        _DELIM = "\n---"
        _chat_buf = ""
        _chat_done = False

        async with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for chunk in stream.text_stream:
                full_text += chunk
                if _chat_done:
                    continue
                _chat_buf += chunk
                idx = _chat_buf.find(_DELIM)
                if idx != -1:
                    # Emit everything before the delimiter, then stop.
                    if idx > 0:
                        await on_chunk(_chat_buf[:idx])
                    _chat_done = True
                else:
                    # Hold back enough chars to catch a delimiter split
                    # across chunk boundaries.
                    hold = len(_DELIM) - 1
                    if len(_chat_buf) > hold:
                        await on_chunk(_chat_buf[:-hold])
                        _chat_buf = _chat_buf[-hold:]

        latency = time.monotonic() - start
        if not _haiku_mode and latency > _LATENCY_THRESHOLD_S and get_settings().fallback_to_haiku:
            logger.warning(
                "Sonnet stream latency %.1fs exceeds threshold — switching to Haiku for this session",
                latency,
            )
            _haiku_mode = True

        try:
            return _parse_vlm_response(full_text)
        except _RetryableError as exc:
            logger.warning("Stream parse failed, falling back to non-stream retry: %s", exc)
            return await analyze_image(image_bytes, system_prompt, user_message=user_message)

    except Exception as exc:
        logger.warning("VLM streaming failed (%s), falling back to non-streaming", exc)
        return await analyze_image(image_bytes, system_prompt, user_message=user_message)


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(
    facing: str,
    building_name: str,
    epicenter_direction: str,
    bearing: float,
    distance_m: float,
    magnitude: float,
    material: str = "unknown",
    height_m: float = 0.0,
    triage_score: float = 0.0,
    color: str = "",
    damage_probability: float = 0.0,
    neighbor_context: str = "",
    cross_reference_context: str = "",
    scenario_prompt: str = "",
) -> str:
    """Format the ATC-20/USAR Structures Specialist system prompt for a scout viewpoint."""
    parts: list[str] = []

    # ---- Role and authority ------------------------------------------------
    parts.append(
        "You are a FEMA USAR Structures Specialist (StS) conducting an ATC-20 Rapid Safety "
        "Evaluation during an active earthquake rescue operation. Your report feeds directly "
        "into Incident Command for rescue team deployment decisions. "
        "Use precise ICS field language — every description must be specific, measurable, "
        "and immediately actionable by a rescue squad leader in the field. "
        "Flag every hazard that could cascade to adjacent rescue sectors."
    )

    # ---- Incident context --------------------------------------------------
    if scenario_prompt:
        parts.append(f"ACTIVE INCIDENT: {scenario_prompt}")
        logger.debug("VLM system prompt: scenario context injected (%d chars)", len(scenario_prompt))
    else:
        logger.debug("VLM system prompt: no scenario context")

    near_field = distance_m < 5_000
    shaking_note = (
        "HIGH near-field shaking intensity — expect severe damage in vulnerable construction types, "
        "particularly URM, non-ductile concrete, and soft-story buildings."
        if near_field
        else f"Moderate-to-high shaking at {distance_m / 1000:.1f}km from epicenter."
    )
    parts.append(
        f"SEISMIC CONTEXT: M{magnitude} earthquake. Epicenter {epicenter_direction} "
        f"at bearing {bearing:.0f}°, {distance_m:.0f}m from this structure. {shaking_note}"
    )

    # ---- Building profile with type-specific flags -------------------------
    mat_lower = material.lower()
    profile_lines = [f"SUBJECT STRUCTURE: {building_name} — {facing} face (ATC-20 Tier 1 Rapid Evaluation)"]

    if material and material != "unknown":
        profile_lines.append(f"Construction type: {material}")

    if height_m > 0:
        stories = max(1, round(height_m / 3.0))
        profile_lines.append(f"Height: {height_m:.0f}m (~{stories} stor{'y' if stories == 1 else 'ies'})")

    if triage_score > 0 and color:
        profile_lines.append(f"Pre-assessment triage: {color} ({triage_score:.0f}/100 score, "
                             f"{damage_probability * 100:.0f}% damage probability)")

    # Material-specific structural warnings
    if any(k in mat_lower for k in ("masonry", "brick", "urm", "stone", "block")):
        profile_lines.append(
            "⚠ UNREINFORCED MASONRY (URM) — highest seismic vulnerability class. "
            "Prioritize: parapet integrity (falling hazard zone = 1× building height from wall base), "
            "out-of-plane wall failure (X-pattern shear cracks, stair-step cracking along mortar joints), "
            "floor/roof beam bearing loss, and chimney condition. "
            "URM parapets are catastrophic falling hazards — cordon off immediately if compromised."
        )
    elif "concrete" in mat_lower:
        profile_lines.append(
            "⚠ CONCRETE FRAME — if pre-1976, assume non-ductile construction. "
            "Check: X-pattern shear cracks at beam-column joints, concrete spalling exposing rebar, "
            "column shortening (punching shear), slab-column connection failure. "
            "Non-ductile concrete fails suddenly and without warning."
        )
    elif "tilt" in mat_lower:
        profile_lines.append(
            "⚠ TILT-UP CONCRETE — check roof-to-wall connection integrity. "
            "Failure mode: roof diaphragm separates, exterior panels fall outward. "
            "Assess gap between wall panels and roof line."
        )
    elif any(k in mat_lower for k in ("wood", "timber", "frame")):
        profile_lines.append(
            "WOOD FRAME — assess: cripple wall failure, soft-story at ground floor (tuck-under parking), "
            "chimney damage and fall zone, roof-to-wall connection. "
            "Look for racked door/window frames indicating story drift."
        )
    elif "steel" in mat_lower:
        profile_lines.append(
            "STEEL FRAME — assess: connection failures at beam flanges, column base plate separation, "
            "lateral drift (racked cladding panels or broken glazing lines), buckling in braced bays."
        )

    if height_m >= 15:
        profile_lines.append(
            "MULTI-STORY: Assess for torsional effects (asymmetric cracking patterns), "
            "soft story at lower floors, and pounding damage if adjacent buildings are present."
        )

    parts.append("\n".join(profile_lines))

    # ---- Inter-sector intelligence (cross-reference) -----------------------
    if cross_reference_context:
        parts.append(cross_reference_context)
    if neighbor_context:
        parts.append(neighbor_context)

    # ---- ATC-20 assessment protocol ----------------------------------------
    parts.append(
        f"ATC-20 RAPID EVALUATION — {facing} face. Assess all visible indicators:\n"
        "\n"
        "SECTION 1 — STRUCTURAL DAMAGE (rate each: None / Minor / Moderate / Severe):\n"
        "□ Collapse or partial collapse; building off foundation\n"
        "□ Building or story leaning / visible lateral drift or rack\n"
        "□ Shear wall cracking: X-pattern diagonal, stair-step through masonry joints, "
        "   diagonal at door/window corners\n"
        "□ Column/beam damage: spalling, exposed rebar, buckled connections, joint failure\n"
        "□ Soft-story: ground floor compressed, door frames racked, open-front vulnerability\n"
        "□ Foundation: differential settlement, tilting, soil gap at base, ground cracking\n"
        "□ Precast/tilt-up: panel separation, connection hardware displaced\n"
        "\n"
        "SECTION 2 — COLLAPSE PATTERN (if any collapse is visible):\n"
        "□ Pancake (floors stacked — very few voids, low survivability)\n"
        "□ Supported lean-to (one end attached — good voids at supported side)\n"
        "□ Unsupported lean-to / cantilever (MOST DANGEROUS — high secondary collapse risk)\n"
        "□ V-shape / A-frame (perimeter voids — moderate survivability)\n"
        "□ Inward/outward wall failure\n"
        "Identify probable survivor void locations and estimated victim access approach.\n"
        "\n"
        "SECTION 3 — NON-STRUCTURAL HAZARDS:\n"
        "□ Parapets, cornices, cladding — falling hazard zone extent (1× building height)\n"
        "□ Broken glazing field — size and scatter radius\n"
        "□ Overhead: power lines, signage, canopies, suspended elements\n"
        "□ Interior non-structural visible through openings: filing cabinets, shelving\n"
        "\n"
        "SECTION 4 — UTILITY HAZARDS (these cascade to adjacent sectors — flag all):\n"
        "□ Natural gas: odor, audible hiss, visible pipe/meter damage, soil staining\n"
        "   Gas follows underground utility corridors — affects structures beyond line-of-sight.\n"
        "   Report direction of utility routing if visible.\n"
        "□ Electrical: downed overhead lines, sparking, damaged pad-mount transformer\n"
        "□ Water main: visible break, soil erosion, foundation undermining\n"
        "\n"
        "SECTION 5 — RESCUE ACCESS:\n"
        "□ Entry point this face — Alpha (front) / Bravo (left) / Charlie (rear) / Delta (right)\n"
        "□ Condition: clear / partially blocked / structurally compromised / impassable\n"
        "□ Safe approach corridor — specify compass direction and minimum stand-off distance\n"
        "□ Safe rescue team staging position from this face\n"
        "□ Any signs of occupancy or victim indicators (sounds, movement, occupancy at time of event)\n"
        "\n"
        "SECTION 6 — EXTERNAL RISK PROJECTION (mandatory for inter-team coordination):\n"
        "□ Any hazard from THIS building that reaches adjacent structures — type, direction, radius\n"
        "□ Gas/chemical: likely underground migration path and direction\n"
        "□ Structural debris: fall zone radius, direction of maximum hazard\n"
        "□ Fire: if present, direction of spread, wind direction\n"
        "\n"
        "SECTION 7 — ATC-20 POSTING:\n"
        "□ GREEN PLACARD: No apparent danger; building apparently safe for entry\n"
        "□ YELLOW PLACARD: Restricted use — specify exactly which areas/floors are off-limits "
        "   and what restrictions apply (load limits, no personnel below level X, etc.)\n"
        "□ RED PLACARD: UNSAFE — specify imminent danger (collapse risk / gas / structural). "
        "   Post at ALL entrances. Define exclusion zone radius.\n"
        "\n"
        "Begin your response with a single plain-English field radio message (1-2 sentences, "
        "ICS language, no JSON). Start it with \"SITREP: \". "
        "Then write \"---\" on its own line. "
        "Then write the JSON object. All JSON text fields must use ICS plain-language — "
        "specific, measurable, and actionable.\n"
        "{\n"
        '  "findings": [\n'
        '    {\n'
        '      "category": "structural|access|overhead|route",\n'
        '      "description": "<ICS field language: specific location, measurement, '
        'actionable implication>",\n'
        '      "severity": "CRITICAL|MODERATE|LOW",\n'
        '      "bbox": [x1, y1, x2, y2] or null\n'
        '    }\n'
        "  ],\n"
        '  "risk_level": "CRITICAL|MODERATE|LOW",\n'
        '  "recommended_action": "<ATC-20 placard + specific entry restrictions + '
        'rescue team staging distance and position>",\n'
        '  "approach_viable": true|false,\n'
        '  "external_risks": [\n'
        '    {\n'
        '      "direction": "<cardinal: N|NE|E|SE|S|SW|W|NW>",\n'
        '      "type": "<gas|electrical|debris|structural|water|fire|chemical>",\n'
        '      "estimated_range_m": <number>\n'
        '    }\n'
        "  ]\n"
        "}"
    )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _RetryableError(Exception):
    """Raised for errors that warrant a retry (rate limit, server error, bad JSON)."""


async def _call_claude(
    model: str,
    system_prompt: str,
    messages: list[dict],
) -> tuple[str, float]:
    """Make a single Anthropic API call. Returns (response_text, latency_seconds)."""
    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    start = time.monotonic()
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
    except anthropic.RateLimitError as exc:
        raise _RetryableError(f"429 rate limit: {exc}") from exc
    except anthropic.InternalServerError as exc:
        raise _RetryableError(f"500 server error: {exc}") from exc

    latency = time.monotonic() - start
    text = response.content[0].text if response.content else ""
    return text, latency


def _parse_vlm_response(raw_text: str) -> VLMAnalysis:
    """Extract and validate JSON from VLM response text.

    Handles markdown code fences. Raises _RetryableError on parse failure.
    """
    # Strip markdown fences if present
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        text = fence_match.group(1)

    # Find the outermost JSON object
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if not brace_match:
        raise _RetryableError(f"No JSON object found in VLM response: {raw_text[:200]!r}")

    try:
        data = json.loads(brace_match.group())
    except json.JSONDecodeError as exc:
        raise _RetryableError(f"JSON decode error: {exc}") from exc

    # Coerce and validate findings
    findings: list[Finding] = []
    for raw_f in data.get("findings", []):
        if not isinstance(raw_f, dict):
            continue
        category = str(raw_f.get("category", "structural")).lower()
        if category not in _VALID_CATEGORIES:
            category = "structural"
        severity = str(raw_f.get("severity", "MODERATE")).upper()
        if severity not in _VALID_SEVERITIES:
            severity = "MODERATE"
        bbox = raw_f.get("bbox")
        if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4):
            bbox = None
        findings.append(Finding(
            category=category,  # type: ignore[arg-type]
            description=str(raw_f.get("description", "")),
            severity=severity,  # type: ignore[arg-type]
            bbox=bbox,
        ))

    risk_level = str(data.get("risk_level", "MODERATE")).upper()
    if risk_level not in _VALID_RISK_LEVELS:
        risk_level = "MODERATE"

    external_risks: list[ExternalRisk] = []
    for raw_r in data.get("external_risks", []):
        if not isinstance(raw_r, dict):
            continue
        try:
            external_risks.append(ExternalRisk(
                direction=str(raw_r.get("direction", "")),
                type=str(raw_r.get("type", "")),
                estimated_range_m=float(raw_r.get("estimated_range_m", 0)),
            ))
        except Exception:
            continue

    return VLMAnalysis(
        findings=findings,
        risk_level=risk_level,  # type: ignore[arg-type]
        recommended_action=str(data.get("recommended_action", "")),
        approach_viable=bool(data.get("approach_viable", True)),
        external_risks=external_risks,
    )


def _fallback_analysis() -> VLMAnalysis:
    """Safe fallback returned when all retries are exhausted."""
    return VLMAnalysis(
        findings=[],
        risk_level="MODERATE",
        recommended_action=(
            "Visual analysis temporarily unavailable. "
            "Proceed with caution and conduct manual assessment."
        ),
        approach_viable=True,
        external_risks=[],
    )
