"""NemoClaw optional augment client — Tasks 7 & 8.

Usage pattern:
    client = await get_nemoclaw_client()
    if client is None:
        # NemoClaw disabled or gateway unreachable — use fallback.
        ...
    else:
        result = await client.call_agent("aegis-crossref", payload)

All public callsites are wrapped in try/except so a gateway error
never breaks the core scout/route path.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal singleton
# ---------------------------------------------------------------------------

_client: "NemoClawClient | None" = None
_connect_attempted: bool = False


class NemoClawClient:
    """Thin wrapper around the NemoClaw SDK gateway connection.

    Instantiated lazily; falls back gracefully on any SDK/network error.
    """

    def __init__(self, gateway_ws_url: str, api_key: str) -> None:
        self._url = gateway_ws_url
        self._api_key = api_key
        self._sdk_client: Any = None  # nemoclaw.NemoClawClient once imported

    async def _ensure_connected(self) -> bool:
        if self._sdk_client is not None:
            return True
        try:
            import nemoclaw  # type: ignore[import]
            self._sdk_client = nemoclaw.NemoClawClient(
                gateway_url=self._url,
                api_key=self._api_key,
            )
            await self._sdk_client.connect()
            logger.info("NemoClaw gateway connected: %s", self._url)
            return True
        except ImportError:
            logger.warning("nemoclaw-sdk not installed — NemoClaw augment unavailable")
        except Exception as exc:
            logger.warning("NemoClaw connect failed: %s", exc)
        return False

    async def call_agent(self, agent_name: str, payload: dict[str, Any], timeout: float = 12.0) -> dict[str, Any] | None:
        """Call a NemoClaw agent and return its structured response, or None on error."""
        if not await self._ensure_connected():
            return None
        try:
            import asyncio
            agent = self._sdk_client.get_agent(agent_name)
            raw = await asyncio.wait_for(agent.run(json.dumps(payload)), timeout=timeout)
            # Agent output may be a JSON string or already a dict.
            if isinstance(raw, str):
                return json.loads(raw)
            return raw
        except Exception as exc:
            logger.warning("NemoClaw agent=%s error: %s", agent_name, exc)
            return None


async def get_nemoclaw_client() -> NemoClawClient | None:
    """Return the singleton NemoClawClient if NemoClaw is enabled, else None."""
    global _client, _connect_attempted
    from ..config import get_settings
    settings = get_settings()

    if not settings.nemoclaw_enabled:
        return None

    if _connect_attempted:
        return _client

    _connect_attempted = True
    if not settings.nemoclaw_gateway_ws_url:
        logger.warning("NEMOCLAW_ENABLED=true but NEMOCLAW_GATEWAY_WS_URL is not set")
        return None

    client = NemoClawClient(settings.nemoclaw_gateway_ws_url, settings.nemoclaw_api_key)
    # Probe connection eagerly so we surface misconfig at startup.
    if await client._ensure_connected():
        _client = client
    return _client


# ---------------------------------------------------------------------------
# Prompt builders
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
    """Compact JSON payload for the aegis-crossref agent."""
    return {
        "from_scout": from_scout_id,
        "to_scout": to_scout_id,
        "risk_type": risk_type,
        "direction": direction,
        "from_building": {"id": from_building_id, "name": from_building_name},
        "to_building": {"name": to_building_name},
        "estimated_range_m": round(estimated_range_m),
    }


def build_route_payload(
    waypoints: list[dict[str, Any]],
    target_building_name: str,
) -> dict[str, Any]:
    """Compact JSON payload for the aegis-route agent (stretch Task 8)."""
    return {
        "target_building": target_building_name,
        "waypoint_count": len(waypoints),
        "waypoints": waypoints,
    }
