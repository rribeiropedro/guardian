"""Scout fleet coordinator — Task 6.

Manages the lifecycle of all Scout instances for a single client session:
  - auto_deploy: stagger-deploys alpha / bravo / charlie to the top-3 triage buildings
  - manual_deploy: deploys an additional scout on demand
  - route_message: forwards a commander question to the named scout
  - cancel_all: tears down all running tasks when a new scenario starts
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..models.schemas import ScoredBuilding
from .scout import Scout

logger = logging.getLogger(__name__)

# Stagger delays for auto-deployed scouts (seconds).
_AUTO_DEPLOY_DELAYS = [0.0, 3.0, 6.0]
# NATO phonetic names, in order.
_SCOUT_NAMES = ["alpha", "bravo", "charlie", "delta", "echo"]


class Coordinator:
    """Owns the scout registry and task lifecycle for one client session."""

    def __init__(self, emit: Callable[[dict], Awaitable[None]]) -> None:
        self._emit = emit
        self.scouts: dict[str, Scout] = {}   # scout_id → Scout
        self._tasks: list[asyncio.Task] = [] # live background tasks
        self._name_counter: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def auto_deploy(
        self,
        buildings: list[ScoredBuilding],
        epicenter_lat: float,
        epicenter_lng: float,
        magnitude: float,
        scenario_prompt: str = "",
    ) -> None:
        """Fire-and-forget: deploy up to 3 scouts with 0 / 3 / 6 s stagger.

        Called immediately after ``triage_result`` is emitted so the frontend
        can render incoming scout events while the commander reviews the map.
        """
        for i, building in enumerate(buildings[:3]):
            delay = _AUTO_DEPLOY_DELAYS[i] if i < len(_AUTO_DEPLOY_DELAYS) else i * 3.0
            scout_id = self._next_scout_id()
            scout = self._make_scout(scout_id, building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)
            self.scouts[scout_id] = scout
            task = asyncio.create_task(
                self._delayed_arrive(scout, delay),
                name=f"scout-{scout_id}-arrive",
            )
            self._register_task(task, scout_id)
            logger.info(
                "Coordinator: queued scout %s → building '%s' (delay=%.0fs)",
                scout_id,
                building.name,
                delay,
            )

    def manual_deploy(
        self,
        building: ScoredBuilding,
        epicenter_lat: float,
        epicenter_lng: float,
        magnitude: float,
        scenario_prompt: str = "",
    ) -> str:
        """Fire-and-forget: deploy one additional scout immediately.

        Returns the assigned scout_id so the caller can log or ack it.
        """
        scout_id = self._next_scout_id()
        scout = self._make_scout(scout_id, building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)
        self.scouts[scout_id] = scout
        task = asyncio.create_task(scout.arrive(), name=f"scout-{scout_id}-arrive")
        self._register_task(task, scout_id)
        logger.info(
            "Coordinator: manual deploy scout %s → building '%s'",
            scout_id,
            building.name,
        )
        return scout_id

    async def deploy_and_await(
        self,
        building: ScoredBuilding,
        epicenter_lat: float,
        epicenter_lng: float,
        magnitude: float,
        scenario_prompt: str = "",
    ) -> str:
        """Deploy one scout and block until arrival completes.

        Used by HTTP dev endpoints where the response must include all emitted
        messages from the arrival sequence.
        """
        scout_id = self._next_scout_id()
        scout = self._make_scout(scout_id, building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)
        self.scouts[scout_id] = scout
        await scout.arrive()
        return scout_id

    def route_message(self, scout_id: str, message: str) -> bool:
        """Fire-and-forget: send a commander question to the named scout.

        Returns ``True`` if the scout exists and the task was scheduled,
        ``False`` if no such scout is registered.
        """
        scout = self.scouts.get(scout_id)
        if scout is None:
            return False
        task = asyncio.create_task(
            scout.handle_question(message),
            name=f"scout-{scout_id}-question",
        )
        self._register_task(task, scout_id)
        return True

    def cancel_all(self) -> None:
        """Cancel all running scout tasks and reset the registry.

        Call this before starting a new scenario for the same client.
        """
        cancelled = 0
        for task in self._tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
        # Also cancel any background auto-survey tasks spawned by scouts.
        for scout in self.scouts.values():
            scout.cancel_survey()
        self._tasks.clear()
        self.scouts.clear()
        self._name_counter = 0
        if cancelled:
            logger.info("Coordinator: cancelled %d running scout tasks", cancelled)

    def get_scout(self, scout_id: str) -> Scout | None:
        return self.scouts.get(scout_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _next_scout_id(self) -> str:
        idx = self._name_counter
        self._name_counter += 1
        return _SCOUT_NAMES[idx] if idx < len(_SCOUT_NAMES) else f"scout-{idx}"

    def _make_scout(
        self,
        scout_id: str,
        building: ScoredBuilding,
        epicenter_lat: float,
        epicenter_lng: float,
        magnitude: float,
        scenario_prompt: str = "",
    ) -> Scout:
        return Scout(
            scout_id=scout_id,
            building=building,
            epicenter_lat=epicenter_lat,
            epicenter_lng=epicenter_lng,
            magnitude=magnitude,
            emit=self._emit,
            scenario_prompt=scenario_prompt,
        )

    def _register_task(self, task: asyncio.Task, scout_id: str) -> None:
        """Attach a done-callback and track the task."""
        self._tasks.append(task)

        def _on_done(t: asyncio.Task) -> None:
            # Remove from task list to avoid unbounded growth
            try:
                self._tasks.remove(t)
            except ValueError:
                pass
            if not t.cancelled() and t.exception():
                logger.error("Scout %s task failed: %s", scout_id, t.exception())

        task.add_done_callback(_on_done)

    @staticmethod
    async def _delayed_arrive(scout: Scout, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await scout.arrive()
