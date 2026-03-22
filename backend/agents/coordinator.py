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
from collections import deque
from collections.abc import Awaitable, Callable

from ..models.schemas import ScoredBuilding
from .scout import Scout
from .state import SharedState, _haversine_m

logger = logging.getLogger(__name__)

# Stagger delays for auto-deployed scouts (seconds).
_AUTO_DEPLOY_DELAYS = [0.0, 3.0, 6.0, 9.0]
# NATO phonetic names, in order.
_SCOUT_NAMES = ["alpha", "bravo", "charlie", "delta", "echo"]


def _build_coverage_queues(
    start_buildings: list[ScoredBuilding],
    remaining: list[ScoredBuilding],
) -> list[deque]:
    """Build one proximity-ordered deque per starting building.

    Step 1 — Voronoi partition: assign each remaining building to the starting
    building whose centroid is nearest.  Each building appears in exactly one
    scout's queue.

    Step 2 — Greedy nearest-neighbour ordering: within each partition, sort by
    a greedy NN walk starting from the initial building position so the scout
    always moves to the closest unvisited site next.

    If ``remaining`` is empty or ``start_buildings`` has fewer than 1 entry,
    returns a list of empty deques — callers treat deque([]) as a no-op.
    """
    if not start_buildings:
        return []

    n = len(start_buildings)
    # Step 1: Voronoi partition
    buckets: list[list[ScoredBuilding]] = [[] for _ in range(n)]
    for b in remaining:
        min_d = float("inf")
        nearest_idx = 0
        for i, s in enumerate(start_buildings):
            d = _haversine_m(s.lat, s.lng, b.lat, b.lng)
            if d < min_d:
                min_d = d
                nearest_idx = i
        buckets[nearest_idx].append(b)

    # Step 2: Greedy NN ordering within each bucket
    queues: list[deque] = []
    for i, start in enumerate(start_buildings):
        unvisited = list(buckets[i])
        ordered: list[ScoredBuilding] = []
        cur_lat, cur_lng = start.lat, start.lng
        while unvisited:
            min_d = float("inf")
            nearest = unvisited[0]
            for b in unvisited:
                d = _haversine_m(cur_lat, cur_lng, b.lat, b.lng)
                if d < min_d:
                    min_d = d
                    nearest = b
            ordered.append(nearest)
            unvisited.remove(nearest)
            cur_lat, cur_lng = nearest.lat, nearest.lng
        queues.append(deque(ordered))

    return queues


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
        # Hard scout-point limit: total analyze_viewpoint() calls across all scouts
        # must not exceed len(all_buildings) * 1.5.  Set in auto_deploy().
        self._scout_point_limit: int = 0
        self._scout_point_count: int = 0
        # Single-fire guard: True once scouts_concluded has been (or is being) emitted.
        self._conclude_fired: bool = False
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

        Each scout receives its own proximity-ordered coverage queue built by
        Voronoi-partitioning the remaining buildings (beyond top-3) and sorting
        each partition by a greedy nearest-neighbour walk from the scout's
        starting position.  Every building appears in exactly one queue so
        coverage is complete with no duplicates.

        Called immediately after ``triage_result`` is emitted.  ``scouts_concluded``
        fires only after every scout has finished its initial survey AND exhausted
        its full building queue — every assigned building is fully assessed first.
        """
        top = buildings[:4]
        count = min(len(top), 4)
        self._auto_arrive_pending = count
        self._scout_point_limit = int(len(buildings) * 1.5)

        # Per-scout proximity-ordered queues; deque([]) when ≤4 buildings total.
        coverage_queues = _build_coverage_queues(top, buildings[4:])

        for i, building in enumerate(top):
            delay = _AUTO_DEPLOY_DELAYS[i] if i < len(_AUTO_DEPLOY_DELAYS) else i * 3.0
            scout_id = self._next_scout_id()
            queue = coverage_queues[i] if i < len(coverage_queues) else deque()
            scout = self._make_scout(
                scout_id, building, epicenter_lat, epicenter_lng, magnitude,
                scenario_prompt, building_queue=queue,
            )
            self.scouts[scout_id] = scout
            self._auto_scout_ids.add(scout_id)
            task = asyncio.create_task(
                self._delayed_arrive(scout, delay),
                name=f"scout-{scout_id}-arrive",
            )
            self._register_task(task, scout_id, is_auto_arrive=True)
            logger.info(
                "Coordinator: queued scout %s → building '%s' (delay=%.0fs, queue=%d buildings)",
                scout_id,
                building.name,
                delay,
                len(queue),
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
        # Cancel initial-building survey tasks and queue continuation tasks.
        for scout in self.scouts.values():
            scout.cancel_survey()
            scout.cancel_queue()
        self._tasks.clear()
        self.scouts.clear()
        self._name_counter = 0
        self._auto_arrive_pending = 0
        self._auto_scout_ids.clear()
        self._scout_point_limit = 0
        self._scout_point_count = 0
        self._conclude_fired = False
        # Null out the callback so stale done-callbacks from the now-cancelled
        # tasks cannot decrement the counter below zero and re-trigger
        # scouts_concluded against the next scenario's state.
        self._on_all_scouts_done = None
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
        building_queue: deque | None = None,
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
            building_queue=building_queue,
            on_scout_point=self._on_scout_point,
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
            # Cancelled tasks are not a failure and must not count toward the
            # conclusion gate — they were torn down by cancel_all() for a new
            # scenario, so firing scouts_concluded here would be premature.
            if t.cancelled():
                return
            if t.exception():
                logger.error("Scout %s task failed: %s", scout_id, t.exception())
            if is_auto_arrive and self._on_all_scouts_done is not None:
                self._auto_arrive_pending -= 1
                if self._auto_arrive_pending <= 0:
                    asyncio.create_task(
                        self._wait_for_surveys_then_conclude(),
                        name="coordinator-conclude",
                    )

        task.add_done_callback(_on_done)

    def _on_scout_point(self) -> None:
        """Called synchronously by a Scout after each completed analyze_viewpoint().

        Increments the global scout-point counter.  When the counter reaches
        ``_scout_point_limit`` (total_buildings * 1.5), cancels all remaining
        survey/queue work across every scout and schedules scouts_concluded.
        The current analyze_viewpoint() call always completes fully before this
        fires — only future iterations are cancelled.
        """
        self._scout_point_count += 1
        logger.info(
            "Coordinator: scout point %d / %d",
            self._scout_point_count,
            self._scout_point_limit,
        )
        if (
            self._scout_point_limit > 0
            and self._scout_point_count >= self._scout_point_limit
            and not self._conclude_fired
            and self._on_all_scouts_done is not None
        ):
            self._conclude_fired = True
            logger.info(
                "Coordinator: scout-point limit reached (%d/%d) — cancelling remaining work",
                self._scout_point_count,
                self._scout_point_limit,
            )
            for scout in self.scouts.values():
                scout.cancel_survey()
                scout.cancel_queue()
            asyncio.create_task(
                self._fire_conclude(),
                name="coordinator-conclude-limit",
            )

    async def _fire_conclude(self) -> None:
        """Emit scouts_concluded exactly once.

        Callers must set ``_conclude_fired = True`` before scheduling this
        coroutine to close the race window between the limit path and the
        normal completion path.
        """
        logger.info("Coordinator: all scouts fully concluded — firing on_all_scouts_done")
        if self._on_all_scouts_done is not None:
            await self._on_all_scouts_done()

    async def _wait_for_surveys_then_conclude(self) -> None:
        """Wait for all auto-scout surveys AND full building queues, then call on_all_scouts_done.

        Step 1 — Awaits survey_task for every auto-scout (initial building, all viewpoints).
        Step 2 — Awaits queue_task for every auto-scout (all remaining queued buildings).
                 queue_task is only spawned after survey_task completes, so collecting it
                 after the survey gather is safe and always captures the task if one exists.

        scouts_concluded fires only after every building is assessed OR the scout-point
        limit fires first (whichever comes first).  The _conclude_fired guard prevents
        double-emission in both directions.
        """
        # If _on_scout_point() already fired the limit path, nothing to do.
        if self._conclude_fired:
            logger.info("Coordinator: _wait_for_surveys_then_conclude — limit already fired, skipping")
            return
        # Claim the slot before the first await so _on_scout_point() racing here
        # will see True and skip its own fire.
        self._conclude_fired = True

        survey_tasks = [
            s.survey_task
            for sid, s in self.scouts.items()
            if sid in self._auto_scout_ids
            and s.survey_task is not None
            and not s.survey_task.done()
        ]
        if survey_tasks:
            logger.info(
                "Coordinator: all scouts arrived — waiting for %d survey task(s)",
                len(survey_tasks),
            )
            await asyncio.gather(*survey_tasks, return_exceptions=True)

        queue_tasks = [
            s.queue_task
            for sid, s in self.scouts.items()
            if sid in self._auto_scout_ids
            and s.queue_task is not None
            and not s.queue_task.done()
        ]
        if queue_tasks:
            logger.info(
                "Coordinator: surveys done — waiting for %d queue task(s) to finish all buildings",
                len(queue_tasks),
            )
            await asyncio.gather(*queue_tasks, return_exceptions=True)

        await self._fire_conclude()

    @staticmethod
    async def _delayed_arrive(scout: Scout, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await scout.arrive()
