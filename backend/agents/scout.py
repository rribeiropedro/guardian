"""Single-scout execution loop."""
from __future__ import annotations

import asyncio
import base64
import logging
import math
from collections.abc import Awaitable, Callable

from ..models.schemas import (
    ScoutAnalysis,
    ScoutDeployed,
    ScoutReport,
    ScoutViewpoint,
    ScoredBuilding,
    VLMAnalysis,
)
from ..services import annotation, streetview, vlm as vlm_service

logger = logging.getLogger(__name__)

# Cardinal keywords that map to facing values for the handle_question heuristic
_DIRECTION_KEYWORDS: dict[str, list[str]] = {
    "N":  ["north"],
    "S":  ["south"],
    "E":  ["east"],
    "W":  ["west"],
    "NE": ["northeast", "north-east"],
    "NW": ["northwest", "north-west"],
    "SE": ["southeast", "south-east"],
    "SW": ["southwest", "south-west"],
    "N":  ["north", "front"],   # "front" defaults to first viewpoint facing
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
        self.conversation_history: list[dict] = []
        self._epicenter_lat = epicenter_lat
        self._epicenter_lng = epicenter_lng
        self._magnitude = magnitude
        self._emit = emit
        self._current_image_bytes: bytes | None = None

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def arrive(self) -> None:
        """Calculate viewpoints, emit scout_deployed, analyze first viewpoint, emit report."""
        self.viewpoints = streetview.calculate_viewpoints(
            self.building.footprint,
            self._epicenter_lat,
            self._epicenter_lng,
        )

        if not self.viewpoints:
            logger.error("Scout %s: no viewpoints calculated for building %s", self.scout_id, self.building.id)
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

    async def analyze_viewpoint(self, viewpoint: ScoutViewpoint) -> ScoutReport:
        """Fetch Street View image, call VLM, annotate, return ScoutReport."""
        image_bytes = await streetview.fetch_street_view_image(
            viewpoint.lat,
            viewpoint.lng,
            viewpoint.heading,
            viewpoint.pitch,
        )
        self._current_image_bytes = image_bytes

        system_prompt = self._build_system_prompt(viewpoint)
        vlm_result = await vlm_service.analyze_image(
            image_bytes,
            system_prompt,
            self.conversation_history,
        )

        # Persist assistant turn to conversation history
        self.conversation_history.append({
            "role": "assistant",
            "content": vlm_result.model_dump_json(),
        })

        annotated_bytes = await annotation.annotate_image(image_bytes, vlm_result.findings)
        image_b64 = base64.b64encode(annotated_bytes).decode()

        scout_analysis = self._vlm_to_scout_analysis(vlm_result)
        narrative = f"[{vlm_result.risk_level}] {vlm_result.recommended_action}"

        return ScoutReport(
            scout_id=self.scout_id,
            building_id=self.building.id,
            viewpoint=viewpoint,
            analysis=scout_analysis,
            annotated_image_b64=image_b64,
            narrative=narrative,
        )

    async def advance(self) -> ScoutReport | None:
        """Move to next viewpoint, analyze it, emit report. Returns None if exhausted."""
        next_index = self.current_viewpoint_index + 1
        if next_index >= len(self.viewpoints):
            logger.debug("Scout %s: no more viewpoints", self.scout_id)
            return None

        self.current_viewpoint_index = next_index
        report = await self.analyze_viewpoint(self.viewpoints[self.current_viewpoint_index])
        await self._emit(report.model_dump())
        return report

    async def handle_question(self, message: str) -> ScoutReport:
        """Handle a commander question. Advances to a new viewpoint if the question asks
        about a direction not yet visited, otherwise re-analyzes the current viewpoint
        with the full conversation context.
        """
        # Append the commander's question to history
        self.conversation_history.append({"role": "user", "content": message})

        # Heuristic: detect if the question requests a specific unvisited facing
        requested_facing = self._detect_requested_facing(message)
        if requested_facing:
            target_idx = self._find_viewpoint_by_facing(requested_facing)
            if target_idx is not None and target_idx != self.current_viewpoint_index:
                self.current_viewpoint_index = target_idx
                report = await self.analyze_viewpoint(self.viewpoints[target_idx])
                await self._emit(report.model_dump())
                return report

        # Re-analyze current viewpoint with accumulated conversation history
        current_vp = self.viewpoints[self.current_viewpoint_index]
        image_bytes = self._current_image_bytes
        if image_bytes is None:
            image_bytes = await streetview.fetch_street_view_image(
                current_vp.lat, current_vp.lng, current_vp.heading, current_vp.pitch,
            )
            self._current_image_bytes = image_bytes

        system_prompt = self._build_system_prompt(current_vp)
        vlm_result = await vlm_service.analyze_image(
            image_bytes,
            system_prompt,
            self.conversation_history,
        )

        self.conversation_history.append({
            "role": "assistant",
            "content": vlm_result.model_dump_json(),
        })

        annotated_bytes = await annotation.annotate_image(image_bytes, vlm_result.findings)
        image_b64 = base64.b64encode(annotated_bytes).decode()

        scout_analysis = self._vlm_to_scout_analysis(vlm_result)
        narrative = f"[{vlm_result.risk_level}] {vlm_result.recommended_action}"

        report = ScoutReport(
            scout_id=self.scout_id,
            building_id=self.building.id,
            viewpoint=current_vp,
            analysis=scout_analysis,
            annotated_image_b64=image_b64,
            narrative=narrative,
        )
        await self._emit(report.model_dump())
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self, viewpoint: ScoutViewpoint) -> str:
        return vlm_service.build_system_prompt(
            facing=viewpoint.facing,
            building_name=self.building.name,
            epicenter_direction=self._epicenter_cardinal(),
            bearing=self._bearing_to_epicenter(),
            distance_m=self._distance_to_epicenter(),
            magnitude=self._magnitude,
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
        lat1, lng1 = math.radians(self.building.lat), math.radians(self.building.lng)
        lat2, lng2 = math.radians(self._epicenter_lat), math.radians(self._epicenter_lng)
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def _bearing_to_epicenter(self) -> float:
        """Compass bearing (0–360) from building to epicenter."""
        lat1 = math.radians(self.building.lat)
        lat2 = math.radians(self._epicenter_lat)
        dlng = math.radians(self._epicenter_lng - self.building.lng)
        x = math.sin(dlng) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    def _epicenter_cardinal(self) -> str:
        """Cardinal direction from building toward the epicenter."""
        bearing = self._bearing_to_epicenter()
        cardinals = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return cardinals[round(bearing / 45) % 8]

    def _detect_requested_facing(self, message: str) -> str | None:
        """Return a facing string if the message mentions a specific direction."""
        msg_lower = message.lower()
        for facing, keywords in _DIRECTION_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                return facing
        return None

    def _find_viewpoint_by_facing(self, facing: str) -> int | None:
        """Return index of the first viewpoint with the given facing, or None."""
        for i, vp in enumerate(self.viewpoints):
            if vp.facing == facing:
                return i
        return None
