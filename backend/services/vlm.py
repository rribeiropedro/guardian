"""VLM service — wraps Anthropic Claude Sonnet vision API for scout facade analysis."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
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
) -> VLMAnalysis:
    """Call Claude vision API and return a structured VLMAnalysis.

    Retries up to _MAX_RETRIES times on 429/500 or JSON parse failure.
    Falls back to Haiku if Sonnet latency exceeds _LATENCY_THRESHOLD_S.
    Returns a safe fallback VLMAnalysis if all retries are exhausted.
    """
    global _haiku_mode

    history = list(conversation_history or [])
    image_b64 = base64.standard_b64encode(image_bytes).decode()

    # Build the user message: image + instruction to return JSON
    user_content: list[dict[str, Any]] = [
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
                "Return ONLY a JSON object matching the specified schema — no markdown, no commentary."
            ),
        },
    ]

    messages = history + [{"role": "user", "content": user_content}]

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
    neighbor_context: str = "",
    cross_reference_context: str = "",
) -> str:
    """Format the standard VLM system prompt for a scout viewpoint."""
    parts = [
        f"You are analyzing the {facing} facade of {building_name}.",
        f"Epicenter is to the {epicenter_direction} (bearing {bearing:.0f}°), "
        f"{distance_m:.0f}m away, magnitude {magnitude}.",
    ]
    if neighbor_context:
        parts.append(neighbor_context)
    if cross_reference_context:
        parts.append(cross_reference_context)
    parts.append(
        "Analyze this facade for:\n"
        "1. Construction type visible (masonry, steel, glass, concrete)\n"
        "2. Structural vulnerability indicators (parapets, overhangs, soft stories)\n"
        "3. Access points (doors, loading docks, parking approaches)\n"
        "4. Overhead hazards (trees, power lines, signage, canopies)\n"
        "5. Route obstructions visible\n"
        "\nReturn ONLY a JSON object with this exact structure:\n"
        "{\n"
        '  "findings": [\n'
        '    {"category": "structural|access|overhead|route", '
        '"description": "string", "severity": "CRITICAL|MODERATE|LOW", '
        '"bbox": [x1,y1,x2,y2] or null}\n'
        "  ],\n"
        '  "risk_level": "CRITICAL|MODERATE|LOW",\n'
        '  "recommended_action": "string",\n'
        '  "approach_viable": true|false,\n'
        '  "external_risks": [\n'
        '    {"direction": "string", "type": "string", "estimated_range_m": number}\n'
        "  ]\n"
        "}"
    )
    return "\n".join(parts)


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
