"""Single-scout execution loop — Task 5."""
from __future__ import annotations

import asyncio
import base64
import logging
import math
from collections.abc import Awaitable, Callable

from ..models.schemas import (
    AgentStreamChunk,
    AgentStreamEnd,
    AgentStreamStart,
    CrossReference,
    ScoutAnalysis,
    ScoutDeployed,
    ScoutReport,
    ScoutViewpoint,
    ScoredBuilding,
    VLMAnalysis,
)
from ..services import annotation, streetview, vlm as vlm_service
from .state import _haversine_m, get_shared_state

logger = logging.getLogger(__name__)

# Cardinal keyword map for direction detection in handle_question.
# "front" is aliased to N so "check the front" navigates to the first viewpoint.
_DIRECTION_KEYWORDS: dict[str, list[str]] = {
    "N":  ["north", "front"],
    "S":  ["south"],
    "E":  ["east"],
    "W":  ["west"],
    "NE": ["northeast", "north-east"],
    "NW": ["northwest", "north-west"],
    "SE": ["southeast", "south-east"],
    "SW": ["southwest", "south-west"],
}


class Scout:
    def __init__(
        self,
        scout_id: str,
        building: ScoredBuilding,
        epicenter_lat: float,
        epicenter_lng: float,
        magnitude: float,
        emit: Callable[[dict], Awaitable[None]],
        scenario_prompt: str = "",
    ) -> None:
        self.scout_id = scout_id
        self.building = building
        self.viewpoints: list[ScoutViewpoint] = []
        self.current_viewpoint_index: int = 0
        self._epicenter_lat = epicenter_lat
        self._epicenter_lng = epicenter_lng
        self._magnitude = magnitude
        self._emit = emit
        self._scenario_prompt = scenario_prompt
        self._current_image_bytes: bytes | None = None
        # Text summaries of completed analyses — injected as context for follow-up questions.
        self._analysis_summaries: list[str] = []
        # Track emitted cross-reference pairs so we don't duplicate (from_scout, to_scout).
        self._emitted_cross_refs: set[tuple[str, str]] = set()
        # Background auto-survey task — kept so Coordinator can cancel it.
        self._survey_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def arrive(self) -> None:
        """Full arrival sequence:
        1. Calculate viewpoints.
        2. Subscribe to SharedState push notifications from peer scouts.
        3. Emit scout_deployed(arriving).
        4. Analyze the epicenter-facing viewpoint.
        5. Emit scout_report.
        6. Emit scout_deployed(active).
        7. Launch background auto-survey of remaining viewpoints.
        """
        logger.info(
            "SCOUT %s arriving at building=%s prompt=%r",
            self.scout_id, self.building.id,
            self._scenario_prompt[:80] if self._scenario_prompt else "(none)",
        )
        self.viewpoints = streetview.calculate_viewpoints(
            self.building.footprint,
            self._epicenter_lat,
            self._epicenter_lng,
        )

        if not self.viewpoints:
            logger.error(
                "Scout %s: no viewpoints for building %s",
                self.scout_id,
                self.building.id,
            )
            return

        # Register push callback so this scout is immediately notified when any
        # peer writes external-risk findings that reach this building's position.
        get_shared_state().subscribe(self._on_peer_risk)

        await self._emit(
            ScoutDeployed(
                scout_id=self.scout_id,
                building_id=self.building.id,
                building_name=self.building.name,
                status="arriving",
            ).model_dump()
        )

        report = await self.analyze_viewpoint(self.viewpoints[0])
        await self._emit(report.model_dump())

        # Signal that the scout has completed its initial assessment and is ready
        # for commander questions or further viewpoint advances.
        await self._emit(
            ScoutDeployed(
                scout_id=self.scout_id,
                building_id=self.building.id,
                building_name=self.building.name,
                status="active",
            ).model_dump()
        )

        # Autonomously survey remaining faces in the background so the full
        # building perimeter is covered without waiting for commander questions.
        # This also triggers more cross-reference discoveries.
        if len(self.viewpoints) > 1:
            self._survey_task = asyncio.create_task(
                self._auto_survey(),
                name=f"scout-{self.scout_id}-survey",
            )

    def cancel_survey(self) -> None:
        """Cancel the background auto-survey task if still running."""
        if self._survey_task and not self._survey_task.done():
            self._survey_task.cancel()

    async def analyze_viewpoint(self, viewpoint: ScoutViewpoint) -> ScoutReport:
        """Core analysis loop for a single viewpoint.

        Flow:
          1. Query SharedState for cross-reference context from nearby scouts.
          2. Fetch Street View image.
          3. Call VLM with cross-ref-enriched system prompt.
          4. Write external_risks to SharedState.
          5. Emit cross_reference messages for any newly-relevant nearby findings.
          6. Annotate image and return ScoutReport.
        """
        state = get_shared_state()

        cross_ref_context = state.format_cross_ref_context(
            self.building.lat,
            self.building.lng,
            exclude_scout_id=self.scout_id,
        )

        image_bytes = await streetview.fetch_street_view_image(
            viewpoint.lat,
            viewpoint.lng,
            viewpoint.heading,
            viewpoint.pitch,
        )
        self._current_image_bytes = image_bytes

        system_prompt = self._build_system_prompt(viewpoint, cross_ref_context=cross_ref_context)
        vlm_result = await self._stream_vlm(image_bytes, system_prompt)

        # Store SITREP entry for conversation context in handle_question.
        critical_findings = [f for f in vlm_result.findings if f.severity == "CRITICAL"]
        self._analysis_summaries.append(
            f"[{viewpoint.facing} face | {vlm_result.risk_level}] "
            f"{len(vlm_result.findings)} finding(s)"
            + (f", {len(critical_findings)} CRITICAL" if critical_findings else "")
            + f" | Action: {vlm_result.recommended_action[:140]}"
        )

        # Persist external risks for other scouts to discover via cross-reference.
        if vlm_result.external_risks:
            state.write_findings(
                scout_id=self.scout_id,
                building_id=self.building.id,
                building_name=self.building.name,
                lat=self.building.lat,
                lng=self.building.lng,
                external_risks=vlm_result.external_risks,
            )

        # Emit cross_reference messages for nearby findings from other scouts.
        nearby = state.query_nearby(
            self.building.lat,
            self.building.lng,
            exclude_scout_id=self.scout_id,
        )
        for record in nearby:
            key = (record.scout_id, self.scout_id)
            if key not in self._emitted_cross_refs:
                finding, impact, resolution = await self._enrich_cross_ref(record)
                await self._emit(
                    CrossReference(
                        from_scout=record.scout_id,
                        to_scout=self.scout_id,
                        finding=finding,
                        impact=impact,
                        resolution=resolution,
                    ).model_dump()
                )
                self._emitted_cross_refs.add(key)

        annotated_bytes = await annotation.annotate_image(image_bytes, vlm_result.findings)
        image_b64 = base64.b64encode(annotated_bytes).decode()

        return ScoutReport(
            scout_id=self.scout_id,
            building_id=self.building.id,
            viewpoint=viewpoint,
            analysis=self._vlm_to_scout_analysis(vlm_result),
            annotated_image_b64=image_b64,
            narrative=f"[{vlm_result.risk_level}] {vlm_result.recommended_action}",
        )

    async def advance(self) -> ScoutReport | None:
        """Move to the next viewpoint, analyze it, emit report.

        Returns None if all viewpoints have been visited.
        """
        next_index = self.current_viewpoint_index + 1
        if next_index >= len(self.viewpoints):
            logger.debug("Scout %s: no more viewpoints to advance to", self.scout_id)
            return None

        self.current_viewpoint_index = next_index
        report = await self.analyze_viewpoint(self.viewpoints[self.current_viewpoint_index])
        await self._emit(report.model_dump())
        return report

    async def handle_question(self, message: str) -> ScoutReport:
        """Handle a commander question.

        If the question requests a specific cardinal direction that hasn't been
        visited yet, navigate there and emit a new report.

        Otherwise, re-analyze the current viewpoint. Previous analysis summaries
        are injected into the system prompt so the VLM has full conversation
        context — avoiding the multi-turn message-format requirements of the
        Anthropic API while preserving meaningful context.
        """
        # Heuristic: advance to a specific facing if the commander requests one.
        requested_facing = self._detect_requested_facing(message)
        if requested_facing:
            target_idx = self._find_viewpoint_by_facing(requested_facing)
            if target_idx is not None and target_idx != self.current_viewpoint_index:
                self.current_viewpoint_index = target_idx
                report = await self.analyze_viewpoint(self.viewpoints[target_idx])
                await self._emit(report.model_dump())
                return report

        # Re-analyze the current viewpoint with the commander's question as context.
        current_vp = self.viewpoints[self.current_viewpoint_index]
        image_bytes = self._current_image_bytes
        if image_bytes is None:
            image_bytes = await streetview.fetch_street_view_image(
                current_vp.lat, current_vp.lng, current_vp.heading, current_vp.pitch,
            )
            self._current_image_bytes = image_bytes

        state = get_shared_state()
        cross_ref_context = state.format_cross_ref_context(
            self.building.lat, self.building.lng, exclude_scout_id=self.scout_id,
        )
        system_prompt = self._build_system_prompt(current_vp, cross_ref_context=cross_ref_context)

        # Inject running SITREP log into the system prompt — preserves field
        # context without requiring multi-turn image messages to the API.
        if self._analysis_summaries:
            prev = "\n".join(f"  {s}" for s in self._analysis_summaries[-4:])
            system_prompt = (
                f"{system_prompt}\n\n"
                f"RUNNING SITREP — prior assessments at {self.building.name}:\n{prev}\n"
                f"Commander question (answer this in recommended_action and findings): {message}"
            )

        vlm_result = await self._stream_vlm(image_bytes, system_prompt, user_message=message)

        self._analysis_summaries.append(
            f"[{current_vp.facing} follow-up] Q='{message[:60]}' "
            f"risk={vlm_result.risk_level}, action={vlm_result.recommended_action[:80]}"
        )

        annotated_bytes = await annotation.annotate_image(image_bytes, vlm_result.findings)
        image_b64 = base64.b64encode(annotated_bytes).decode()

        report = ScoutReport(
            scout_id=self.scout_id,
            building_id=self.building.id,
            viewpoint=current_vp,
            analysis=self._vlm_to_scout_analysis(vlm_result),
            annotated_image_b64=image_b64,
            narrative=f"[{vlm_result.risk_level}] {vlm_result.recommended_action}",
        )
        await self._emit(report.model_dump())
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _stream_vlm(
        self,
        image_bytes: bytes,
        system_prompt: str,
        user_message: str | None = None,
    ) -> VLMAnalysis:
        """Call VLM with streaming, emitting agent_stream_* messages to the frontend.

        Emits agent_stream_start, then agent_stream_chunk for each token,
        then agent_stream_end. Returns the fully parsed VLMAnalysis.
        """
        seq = 0

        await self._emit(
            AgentStreamStart(
                scout_id=self.scout_id,
                building_id=self.building.id,
            ).model_dump()
        )

        async def on_chunk(chunk: str) -> None:
            nonlocal seq
            await self._emit(
                AgentStreamChunk(
                    scout_id=self.scout_id,
                    building_id=self.building.id,
                    chunk=chunk,
                    sequence=seq,
                ).model_dump()
            )
            seq += 1

        result = await vlm_service.analyze_image_stream(
            image_bytes, system_prompt, on_chunk=on_chunk, user_message=user_message
        )

        await self._emit(
            AgentStreamEnd(
                scout_id=self.scout_id,
                building_id=self.building.id,
            ).model_dump()
        )

        return result

    async def _enrich_cross_ref(self, record: object) -> tuple[str, str, str | None]:
        """Return (finding, impact, resolution) for a cross-reference record.

        If OpenClaw cloud is enabled and available, calls the aegis-crossref agent to
        produce richer narrative text. Falls back to ICS-format template strings
        on failure or when OpenClaw is disabled (default).
        """
        risk_type: str = record.risk_type  # type: ignore[attr-defined]
        direction: str = record.direction  # type: ignore[attr-defined]
        range_m: float = record.estimated_range_m  # type: ignore[attr-defined]
        from_building: str = getattr(record, "building_name", record.building_id)  # type: ignore[attr-defined]

        from_callsign = f"SCOUT-{record.scout_id.upper()}"  # type: ignore[attr-defined]
        to_callsign = f"SCOUT-{self.scout_id.upper()}"

        # Determine if this hazard migrates underground (gas/chemical follow utility corridors)
        _UNDERGROUND_TYPES = {"gas", "chemical", "fuel", "utility"}
        is_underground = any(t in risk_type.lower() for t in _UNDERGROUND_TYPES)

        if is_underground:
            migration_detail = (
                f"Hazard propagates via underground utility corridors — exposure not limited to line-of-sight. "
                f"Monitor foundation penetrations, storm drain access, and manholes within {range_m:.0f}m "
                f"before committing any rescue assets."
            )
            resolution_text = (
                f"Coordinate with {from_callsign} for real-time LEL readings. "
                f"Establish {range_m:.0f}m exclusion zone — no entry until utility confirms shut-off "
                f"and atmospheric monitoring reads below 10% LEL. All units hold staging positions "
                f"pending Utilities notification. Safety Officer notification required."
            )
        else:
            migration_detail = (
                f"Direct-exposure hazard, {range_m:.0f}m radius. "
                f"Evaluate shared approach corridor and exposure zone prior to any advance."
            )
            resolution_text = (
                f"Stage minimum {range_m:.0f}m from shared boundary with {from_building}. "
                f"Confirm clearance with {from_callsign} before committing assets to {direction} corridor."
            )

        template_finding = (
            f"{from_callsign} to {to_callsign}: Confirmed {risk_type.upper()} hazard at {from_building}. "
            f"Hazard vector {direction}, estimated exposure radius {range_m:.0f}m. {migration_detail}"
        )
        template_impact = (
            f"BE ADVISED — {to_callsign} sector ({self.building.name}) lies within {risk_type} "
            f"hazard projection from {from_callsign} sector ({from_building}), {range_m:.0f}m radius. "
            f"{direction} approach corridor COMPROMISED. "
            f"Requesting utility coordination and Safety Officer notification. Standby for updated access assessment."
        )

        try:
            from ..services.openclaw_client import build_crossref_prompt, get_openclaw_client
            nc = await get_openclaw_client()
            if nc is None:
                return template_finding, template_impact, None

            prompt = build_crossref_prompt(
                from_scout_id=record.scout_id,  # type: ignore[attr-defined]
                to_scout_id=self.scout_id,
                risk_type=record.risk_type,  # type: ignore[attr-defined]
                direction=record.direction,  # type: ignore[attr-defined]
                from_building_id=record.building_id,  # type: ignore[attr-defined]
                from_building_name=from_building,
                to_building_name=self.building.name,
                estimated_range_m=record.estimated_range_m,  # type: ignore[attr-defined]
            )
            result = await nc.call_agent("aegis-crossref", prompt)
            if result is None:
                return template_finding, template_impact, None

            finding = str(result.get("finding", template_finding))
            impact = str(result.get("impact", template_impact))
            resolution = result.get("resolution")
            return finding, impact, str(resolution) if resolution else None
        except Exception as exc:
            logger.warning("OpenClaw crossref enrichment failed: %s", exc)
            return template_finding, template_impact, None

    async def _auto_survey(self) -> None:
        """Background task: walk remaining viewpoints after initial arrival.

        Calls ``advance()`` in a loop so every building face is analysed and
        emitted as a ``scout_report``.  Each cycle also queries SharedState for
        cross-references, increasing the probability that hazards from peer
        scouts are picked up and broadcast before the commander sends questions.
        """
        try:
            while True:
                report = await self.advance()
                if report is None:
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Scout %s: auto_survey failed", self.scout_id)

    async def _on_peer_risk(self, record: object) -> None:
        """Push notification: a peer scout just persisted new external-risk findings.

        Called by SharedState immediately when ``write_findings`` is invoked by
        another scout.  If the new risk record's projected radius reaches this
        building, emits a ``cross_reference`` WebSocket message right away —
        without waiting for the next ``analyze_viewpoint`` cycle.
        """
        if record.scout_id == self.scout_id:  # type: ignore[attr-defined]
            return

        dist = _haversine_m(
            record.origin_lat,  # type: ignore[attr-defined]
            record.origin_lng,  # type: ignore[attr-defined]
            self.building.lat,
            self.building.lng,
        )
        if dist > record.estimated_range_m:  # type: ignore[attr-defined]
            return

        key = (record.scout_id, self.scout_id)  # type: ignore[attr-defined]
        if key in self._emitted_cross_refs:
            return
        # Guard against concurrent duplicate emissions (asyncio cooperative, but safe).
        self._emitted_cross_refs.add(key)

        try:
            finding, impact, resolution = await self._enrich_cross_ref(record)
            await self._emit(
                CrossReference(
                    from_scout=record.scout_id,  # type: ignore[attr-defined]
                    to_scout=self.scout_id,
                    finding=finding,
                    impact=impact,
                    resolution=resolution,
                ).model_dump()
            )
            logger.info(
                "Scout %s: push cross-ref from %s — %s",
                self.scout_id,
                record.scout_id,  # type: ignore[attr-defined]
                record.risk_type,  # type: ignore[attr-defined]
            )
        except Exception:
            logger.exception(
                "Scout %s: failed to emit push cross-ref from %s",
                self.scout_id,
                record.scout_id,  # type: ignore[attr-defined]
            )

    def _build_system_prompt(
        self,
        viewpoint: ScoutViewpoint,
        cross_ref_context: str = "",
    ) -> str:
        return vlm_service.build_system_prompt(
            facing=viewpoint.facing,
            building_name=self.building.name,
            epicenter_direction=self._epicenter_cardinal(),
            bearing=self._bearing_to_epicenter(),
            distance_m=self._distance_to_epicenter(),
            magnitude=self._magnitude,
            material=self.building.material,
            height_m=self.building.height_m,
            triage_score=self.building.triage_score,
            color=self.building.color,
            damage_probability=self.building.damage_probability,
            cross_reference_context=cross_ref_context,
            scenario_prompt=self._scenario_prompt,
        )

    def _vlm_to_scout_analysis(self, vlm_result: VLMAnalysis) -> ScoutAnalysis:
        return ScoutAnalysis(
            risk_level=vlm_result.risk_level,
            findings=vlm_result.findings,
            recommended_action=vlm_result.recommended_action,
            approach_viable=vlm_result.approach_viable,
        )

    def _distance_to_epicenter(self) -> float:
        """Haversine distance in metres from building centroid to epicenter."""
        R = 6_371_000.0
        lat1 = math.radians(self.building.lat)
        lat2 = math.radians(self._epicenter_lat)
        lng1 = math.radians(self.building.lng)
        lng2 = math.radians(self._epicenter_lng)
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def _bearing_to_epicenter(self) -> float:
        """Compass bearing (0–360) from building centroid toward epicenter."""
        lat1 = math.radians(self.building.lat)
        lat2 = math.radians(self._epicenter_lat)
        dlng = math.radians(self._epicenter_lng - self.building.lng)
        x = math.sin(dlng) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def _epicenter_cardinal(self) -> str:
        """Cardinal direction from building toward the epicenter."""
        cardinals = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return cardinals[round(self._bearing_to_epicenter() / 45) % 8]

    def _detect_requested_facing(self, message: str) -> str | None:
        """Return a facing string if the message mentions a cardinal direction."""
        msg_lower = message.lower()
        for facing, keywords in _DIRECTION_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                return facing
        return None

    def _find_viewpoint_by_facing(self, facing: str) -> int | None:
        """Return the index of the first viewpoint with the given facing, or None."""
        for i, vp in enumerate(self.viewpoints):
            if vp.facing == facing:
                return i
        return None
