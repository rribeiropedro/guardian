"""OpenClaw cloud HTTP client.

Replaces the NemoClaw ACP WebSocket client.  The OpenClaw cloud gateway
exposes a simple REST endpoint for spawning sub-agent sessions:

    POST {OPENCLAW_GATEWAY_URL}/api/sessions
    Authorization: Bearer {OPENCLAW_API_KEY}
    Content-Type: application/json

    {
      "runtime":  "subagent",
      "agentId":  "aegis-route",
      "task":     "<prompt with all context>",
      "mode":     "run",
      "cleanup":  "delete"
    }

The gateway runs the agent to completion and returns the result.
The agent's JSON output is extracted from the response and returned as a dict.

Usage:
    client = await get_openclaw_client()
    if client:
        result = await client.call_agent("aegis-crossref", prompt)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OpenClawClient:
    """Thin async HTTP client for OpenClaw cloud sub-agent sessions."""

    def __init__(self, gateway_url: str, api_key: str) -> None:
        # Strip trailing slash so we can safely append paths.
        self._url = gateway_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def call_agent(
        self,
        agent_id: str,
        task: str,
        timeout: float = 120.0,
    ) -> dict[str, Any] | None:
        """Spawn a cloud sub-agent session and return its parsed JSON output.

        Parameters
        ----------
        agent_id:
            The registered agent name (e.g. "aegis-route", "aegis-crossref").
        task:
            The full task prompt, including any serialized context data.
        timeout:
            Total HTTP timeout in seconds (agent inference + network).

        Returns
        -------
        Parsed JSON dict from the agent response, or None on any error.
        """
        payload = {
            "runtime": "subagent",
            "agentId": agent_id,
            "task": task,
            "mode": "run",
            "cleanup": "delete",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                resp = await http.post(
                    f"{self._url}/api/sessions",
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenClaw agent=%s HTTP %d: %s",
                agent_id, exc.response.status_code, exc.response.text[:200],
            )
            return None
        except Exception as exc:
            logger.warning("OpenClaw agent=%s request failed: %s", agent_id, exc)
            return None

        body = resp.json()

        # The gateway may nest the agent output under different keys depending
        # on gateway version.  Try the most common shapes.
        raw_output: str | dict | None = (
            body.get("result")
            or body.get("output")
            or body.get("response")
            or body.get("content")
        )

        if raw_output is None:
            logger.warning("OpenClaw agent=%s: no result field in response: %s", agent_id, str(body)[:200])
            return None

        if isinstance(raw_output, dict):
            return raw_output

        return _parse_json(str(raw_output))


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_client: OpenClawClient | None = None
_init_done: bool = False


async def get_openclaw_client() -> OpenClawClient | None:
    """Return the singleton OpenClawClient if cloud is enabled, else None."""
    global _client, _init_done
    from ..config import get_settings
    settings = get_settings()

    if not settings.openclaw_enabled:
        return None

    if _init_done:
        return _client

    _init_done = True

    if not settings.openclaw_gateway_url:
        logger.warning("OPENCLAW_ENABLED=true but OPENCLAW_GATEWAY_URL is not set")
        _client = None
        return None

    _client = OpenClawClient(settings.openclaw_gateway_url, settings.openclaw_api_key)
    logger.info("OpenClaw cloud client ready (gateway=%s)", settings.openclaw_gateway_url)
    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from agent response, stripping markdown fences if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
        stripped = m.group(1) if m else stripped
    try:
        result = json.loads(stripped)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Prompt builders (shared by scout.py and route_agent.py)
# ---------------------------------------------------------------------------

def build_crossref_payload(
    from_scout_id: str,
    to_scout_id: str,
    risk_type: str,
    direction: str,
    from_building_id: str,
    from_building_name: str,
    to_building_name: str,
    estimated_range_m: float,
) -> dict[str, Any]:
    return {
        "from_scout": from_scout_id,
        "to_scout": to_scout_id,
        "risk_type": risk_type,
        "direction": direction,
        "from_building": {"id": from_building_id, "name": from_building_name},
        "to_building": {"name": to_building_name},
        "estimated_range_m": round(estimated_range_m),
    }


def build_crossref_prompt(
    from_scout_id: str,
    to_scout_id: str,
    risk_type: str,
    direction: str,
    from_building_id: str,
    from_building_name: str,
    to_building_name: str,
    estimated_range_m: float,
) -> str:
    payload = build_crossref_payload(
        from_scout_id, to_scout_id, risk_type, direction,
        from_building_id, from_building_name, to_building_name, estimated_range_m,
    )
    return (
        f"Cross-reference hazard detected:\n{json.dumps(payload, indent=2)}\n\n"
        "Generate the ICS cross-reference report as specified."
    )
