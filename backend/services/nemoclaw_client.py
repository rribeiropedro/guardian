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

    async def call_agent_with_tools(
        self,
        agent_name: str,
        prompt: str,
        tool_definitions: list[dict[str, Any]],
        tool_dispatcher: Any,  # Callable[[str, dict], dict]
        max_turns: int = 12,
        timeout_per_turn: float = 15.0,
    ) -> dict[str, Any] | None:
        """Run a NemoClaw agent in an agentic tool-calling loop.

        The agent receives the prompt and tool definitions, then autonomously
        decides which tools to call and in what order.  This method handles
        the turn loop: receive tool_call → dispatch to Python → send result →
        repeat until the agent emits a final text response or max_turns is hit.

        Parameters
        ----------
        agent_name:
            NemoClaw agent name (e.g. "aegis-route").
        prompt:
            Initial task description sent to the agent.
        tool_definitions:
            List of tool schemas (Anthropic tool-use format) the agent may call.
        tool_dispatcher:
            Callable(tool_name: str, args: dict) → dict.
            Dispatches tool calls from the agent to Python implementations.
        max_turns:
            Maximum agentic turns before giving up and returning None.
        timeout_per_turn:
            Per-turn timeout in seconds.

        Returns
        -------
        dict parsed from the agent's final response, or None on failure.

        TODO: This method's internals depend on the NemoClaw SDK's tool-calling
        API, which is not yet confirmed.  Two likely patterns:

        Pattern A — SDK handles the loop internally:
            agent = self._sdk_client.get_agent(agent_name)
            agent.register_tools(tool_definitions, tool_dispatcher)
            raw = await agent.run_with_tools(prompt, max_turns=max_turns)

        Pattern B — Manual turn loop (shown below as the fallback):
            We implement the loop ourselves by checking if the agent response
            contains a "tool_call" key and dispatching accordingly.

        The fallback below implements Pattern B.  Replace with Pattern A once
        the SDK confirms its interface.
        """
        if not await self._ensure_connected():
            return None

        try:
            import asyncio as _asyncio

            agent = self._sdk_client.get_agent(agent_name)

            # --- Pattern A: try SDK-native tool loop first ---
            if hasattr(agent, "run_with_tools"):
                raw = await _asyncio.wait_for(
                    agent.run_with_tools(
                        prompt,
                        tools=tool_definitions,
                        tool_handler=tool_dispatcher,
                        max_turns=max_turns,
                    ),
                    timeout=timeout_per_turn * max_turns,
                )
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw

            # --- Pattern B: manual turn loop fallback ---
            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            # Include tool definitions in the initial payload.
            initial_payload = {
                "messages": messages,
                "tools": tool_definitions,
            }

            for turn in range(max_turns):
                raw = await _asyncio.wait_for(
                    agent.run(json.dumps(initial_payload if turn == 0 else {"messages": messages})),
                    timeout=timeout_per_turn,
                )
                response = json.loads(raw) if isinstance(raw, str) else raw

                # Check if the agent wants to call a tool.
                tool_call = response.get("tool_call") or response.get("tool_use")
                if tool_call:
                    tool_name = tool_call.get("name") or tool_call.get("tool")
                    tool_args = tool_call.get("input") or tool_call.get("arguments", {})
                    if isinstance(tool_args, str):
                        tool_args = json.loads(tool_args)

                    tool_result = tool_dispatcher(tool_name, tool_args)
                    logger.debug(
                        "NemoClaw agent=%s called tool=%s result_keys=%s",
                        agent_name, tool_name, list(tool_result.keys()),
                    )
                    # Append tool result and continue the loop.
                    messages.append({"role": "assistant", "content": json.dumps(response)})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", tool_name),
                        "content": json.dumps(tool_result),
                    })
                    initial_payload = {}  # subsequent turns use messages only
                    continue

                # No tool call → agent has produced its final answer.
                return response

            logger.warning("NemoClaw agent=%s hit max_turns=%d without final response", agent_name, max_turns)
            return None

        except Exception as exc:
            logger.warning("NemoClaw call_agent_with_tools agent=%s error: %s", agent_name, exc)
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
