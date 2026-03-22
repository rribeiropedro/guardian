"""Scout fleet coordinator — Task 6.

Manages the lifecycle of all Scout instances for a single client session:
  - auto_deploy: stagger-deploys alpha / bravo / charlie to the top-3 triage buildings
  - manual_deploy: deploys an additional scout on demand
  - route_message: forwards a commander question to the named scout
  - cancel_all: tears down all running tasks when a new scenario starts

When all auto-deployed scouts complete both their arrival analysis AND their
background auto-survey, the coordinator fires the optional ``on_all_scouts_done``
coroutine.  The main.py handler uses this to emit ``scouts_concluded`` and
auto-trigger the route walkthrough.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..models.schemas import ScoredBuilding
from .scout import Scout
from .state import SharedState

logger = logging.getLogger(__name__)

# Stagger delays for auto-deployed scouts (seconds).
_AUTO_DEPLOY_DELAYS = [0.0, 3.0, 6.0]
# NATO phonetic names, in order.
_SCOUT_NAMES = ["alpha", "bravo", "charlie", "delta", "echo"]


class Coordinator:
    """Owns the scout registry and task lifecycle for one client session."""

    def __init__(
        self,
        emit: Callable[[dict], Awaitable[None]],
        on_all_scouts_done: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._emit = emit
        self.scouts: dict[str, Scout] = {}   # scout_id → Scout
        self._tasks: list[asyncio.Task] = [] # live background tasks
        self._name_counter: int = 0
        self._on_all_scouts_done = on_all_scouts_done
        # Counts down from the number of auto-deployed scouts; fires the
        # callback once all their arrive() coroutines complete.
        self._auto_arrive_pending: int = 0
        # Ids of scouts registered via auto_deploy (not manual_deploy).
        self._auto_scout_ids: set[str] = set()
        # Per-coordinator SharedState — isolates cross-reference findings
        # from concurrent client sessions that share the same process.
        self.shared_state = SharedState()

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
        When all arrive() tasks complete and their auto-surveys finish,
        ``on_all_scouts_done`` is fired (if set).
        """
        count = min(len(buildings), 3)
        self._auto_arrive_pending = count
        for i, building in enumerate(buildings[:3]):
            delay = _AUTO_DEPLOY_DELAYS[i] if i < len(_AUTO_DEPLOY_DELAYS) else i * 3.0
            scout_id = self._next_scout_id()
            scout = self._make_scout(scout_id, building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)
            self.scouts[scout_id] = scout
            self._auto_scout_ids.add(scout_id)
            task = asyncio.create_task(
                self._delayed_arrive(scout, delay),
                name=f"scout-{scout_id}-arrive",
            )
            self._register_task(task, scout_id, is_auto_arrive=True)
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
        Manual deploys do NOT count toward the all-scouts-done conclusion.
        """
        scout_id = self._next_scout_id()
        scout = self._make_scout(scout_id, building, epicenter_lat, epicenter_lng, magnitude, scenario_prompt)
        self.scouts[scout_id] = scout
        task = asyncio.create_task(scout.arrive(), name=f"scout-{scout_id}-arrive")
        self._register_task(task, scout_id, is_auto_arrive=False)
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
        self._auto_arrive_pending = 0
        self._auto_scout_ids.clear()
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
            shared_state=self.shared_state,
        )

    def _register_task(
        self, task: asyncio.Task, scout_id: str, is_auto_arrive: bool = False
    ) -> None:
        """Attach a done-callback and track the task.

        When ``is_auto_arrive`` is True this task counts toward the completion
        gate that fires ``on_all_scouts_done``.
        """
        self._tasks.append(task)

        def _on_done(t: asyncio.Task) -> None:
            # Remove from task list to avoid unbounded growth
            try:
                self._tasks.remove(t)
            except ValueError:
                pass
            if not t.cancelled() and t.exception():
                logger.error("Scout %s task failed: %s", scout_id, t.exception())
            if is_auto_arrive and self._on_all_scouts_done is not None:
                self._auto_arrive_pending -= 1
                if self._auto_arrive_pending <= 0:
                    asyncio.create_task(
                        self._wait_for_surveys_then_conclude(),
                        name="coordinator-conclude",
                    )

        task.add_done_callback(_on_done)

    async def _wait_for_surveys_then_conclude(self) -> None:
        """Wait for all auto-scout survey tasks to finish, then call on_all_scouts_done."""
        survey_tasks = [
            s.survey_task
            for sid, s in self.scouts.items()
            if sid in self._auto_scout_ids
            and s.survey_task is not None
            and not s.survey_task.done()
        ]
        if survey_tasks:
            logger.info(
                "Coordinator: all scouts arrived — waiting for %d survey task(s) to finish",
                len(survey_tasks),
            )
            await asyncio.gather(*survey_tasks, return_exceptions=True)

        logger.info("Coordinator: all scouts concluded — firing on_all_scouts_done")
        if self._on_all_scouts_done is not None:
            await self._on_all_scouts_done()

    @staticmethod
    async def _delayed_arrive(scout: Scout, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await scout.arrive()
