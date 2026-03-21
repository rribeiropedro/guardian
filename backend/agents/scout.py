"""Single-scout execution loop — Task 5."""
from __future__ import annotations

import asyncio
import base64
import logging
import math
from collections.abc import Awaitable, Callable

from ..models.schemas import (
    CrossReference,
    ScoutAnalysis,
    ScoutDeployed,
    ScoutReport,
    ScoutViewpoint,
    ScoredBuilding,
    VLMAnalysis,
)
from ..services import annotation, streetview, vlm as vlm_service
from .state import get_shared_state

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
    ) -> None:
        self.scout_id = scout_id
        self.building = building
        self.viewpoints: list[ScoutViewpoint] = []
        self.current_viewpoint_index: int = 0
        self._epicenter_lat = epicenter_lat
        self._epicenter_lng = epicenter_lng
        self._magnitude = magnitude
        self._emit = emit
        self._current_image_bytes: bytes | None = None
        # Text summaries of completed analyses — injected as context for follow-up questions.
        self._analysis_summaries: list[str] = []
        # Track emitted cross-reference pairs so we don't duplicate (from_scout, to_scout).
        self._emitted_cross_refs: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def arrive(self) -> None:
        """Full arrival sequence:
        1. Calculate viewpoints.
        2. Emit scout_deployed(arriving).
        3. Analyze the epicenter-facing viewpoint.
        4. Emit scout_report.
        5. Emit scout_deployed(active).
        """
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
        vlm_result = await vlm_service.analyze_image(image_bytes, system_prompt)

        # Store text summary for conversation context in handle_question.
        self._analysis_summaries.append(
            f"[{viewpoint.facing}] risk={vlm_result.risk_level}, "
            f"{len(vlm_result.findings)} finding(s), "
            f"action={vlm_result.recommended_action[:120]}"
        )

        # Persist external risks for other scouts to discover via cross-reference.
        if vlm_result.external_risks:
            state.write_findings(
                scout_id=self.scout_id,
                building_id=self.building.id,
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
                await self._emit(
                    CrossReference(
                        from_scout=record.scout_id,
                        to_scout=self.scout_id,
                        finding=f"{record.risk_type} hazard to the {record.direction}",
                        impact=(
                            f"May affect approach to {self.building.name} "
                            f"(~{record.estimated_range_m:.0f}m range from "
                            f"building {record.building_id})"
                        ),
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

        # Inject previous analysis summaries into the system prompt so context
        # is preserved without requiring multi-turn image messages.
        if self._analysis_summaries:
            prev = "\n".join(f"  {s}" for s in self._analysis_summaries[-3:])
            system_prompt = f"{system_prompt}\n\nPrevious viewpoint analyses:\n{prev}"

        vlm_result = await vlm_service.analyze_image(
            image_bytes,
            system_prompt,
            user_message=message,
        )

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
            cross_reference_context=cross_ref_context,
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
