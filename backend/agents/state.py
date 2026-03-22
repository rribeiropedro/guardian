"""Shared in-memory state for cross-reference detection between scouts."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Leading underscore signals internal use; exported for type annotations in scout.py.
@dataclass
class _RiskRecord:
    scout_id: str
    building_id: str
    building_name: str
    origin_lat: float
    origin_lng: float
    risk_type: str
    direction: str
    estimated_range_m: float


class SharedState:
    """In-memory store for scout external-risk findings.

    Scouts write findings after each VLM analysis. Other scouts query nearby
    findings to inject cross-reference context into their VLM prompts and to
    emit ``cross_reference`` WebSocket messages.
    """

    def __init__(self) -> None:
        self._records: list[_RiskRecord] = []
        # Callbacks registered by active scouts to receive push notifications
        # when another scout writes new external-risk findings.
        self._subscribers: list[Callable[[object], Awaitable[None]]] = []

    def reset_for_scenario(self, scenario_id: str | None = None) -> None:
        """Clear all risk records and push subscribers.

        Call this at the start of each new scenario so stale findings from a
        prior run don't bleed into cross-reference queries for a new client.
        The scenario_id parameter is accepted for logging but not stored —
        the store is intentionally flat (all scouts in the same process share
        one singleton, scoped by reset boundary).
        """
        self._records.clear()
        self._subscribers.clear()

    def subscribe(self, callback: Callable[[object], Awaitable[None]]) -> None:
        """Register a coroutine callback to be called when new risk records are written.

        The callback receives the new ``_RiskRecord`` as its only argument.
        Use this for push-based cross-reference notifications between scouts.
        """
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[object], Awaitable[None]]) -> None:
        """Remove a previously registered push callback."""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def write_findings(
        self,
        scout_id: str,
        building_id: str,
        lat: float,
        lng: float,
        external_risks: list,  # list[ExternalRisk] — typed as list to avoid circular import
        building_name: str = "",
    ) -> None:
        """Persist external risks and push-notify all subscribed peer scouts."""
        for risk in external_risks:
            record = _RiskRecord(
                scout_id=scout_id,
                building_id=building_id,
                building_name=building_name or building_id,
                origin_lat=lat,
                origin_lng=lng,
                risk_type=risk.type,
                direction=risk.direction,
                estimated_range_m=risk.estimated_range_m,
            )
            self._records.append(record)
            # Push-notify peer scouts immediately so they can emit cross_reference
            # messages without waiting for their next analyze_viewpoint call.
            if self._subscribers:
                try:
                    loop = asyncio.get_running_loop()
                    for cb in list(self._subscribers):
                        loop.create_task(cb(record))
                except RuntimeError:
                    # No running event loop — synchronous test context; skip push.
                    pass

    def query_nearby(
        self,
        lat: float,
        lng: float,
        exclude_scout_id: str | None = None,
    ) -> list[_RiskRecord]:
        """Return records from other scouts whose estimated range reaches (lat, lng)."""
        results = []
        for record in self._records:
            if exclude_scout_id and record.scout_id == exclude_scout_id:
                continue
            dist = _haversine_m(record.origin_lat, record.origin_lng, lat, lng)
            if dist <= record.estimated_range_m:
                results.append(record)
        return results

    def get_all_records(self) -> list[_RiskRecord]:
        """Return a snapshot of all current risk records.

        Used by the route handler to pass scout external-risk findings to
        the hazard-zone builder without accessing the private _records list.
        """
        return list(self._records)

    def format_cross_ref_context(
        self,
        lat: float,
        lng: float,
        exclude_scout_id: str | None = None,
    ) -> str:
        """Return an ICS-format inter-sector hazard advisory for VLM prompt injection.

        Returns an empty string when no relevant cross-references exist.
        """
        nearby = self.query_nearby(lat, lng, exclude_scout_id=exclude_scout_id)
        if not nearby:
            return ""

        lines = [
            "INTER-SECTOR HAZARD ADVISORY — live intelligence from overlapping sector scouts:",
            "These findings are from peer scouts deployed SIMULTANEOUSLY in adjacent overlapping zones.",
            "Your sector and theirs share a 25m boundary band — hazards crossing that band affect BOTH teams.",
        ]
        for r in nearby:
            # Classify migration risk — gas/chemical follow utility corridors,
            # not line-of-sight; structural debris is line-of-sight only.
            _UNDERGROUND_TYPES = {"gas", "chemical", "fuel", "utility"}
            is_underground = any(t in r.risk_type.lower() for t in _UNDERGROUND_TYPES)
            migration_note = (
                "Hazard migrates via underground utility corridors — not limited to line-of-sight. "
                "Air-monitor foundation penetrations and manholes within radius. "
                "Coordinate exclusion zone with the reporting scout before any entry."
                if is_underground
                else "Assess shared approach corridor and exposure zone before committing rescue assets. "
                "Confirm clearance with the reporting scout at the overlap boundary."
            )
            lines.append(
                f"  ⚠ SCOUT-{r.scout_id.upper()} | {r.building_name}: CONFIRMED {r.risk_type} hazard "
                f"projecting {r.direction}, ~{r.estimated_range_m:.0f}m radius. "
                f"This structure lies within the projected zone. {migration_note}"
            )

        lines.append(
            "REQUIRED ACTIONS:\n"
            "  1. Acknowledge each advisory above in your recommended_action field.\n"
            "  2. Report in external_risks ANY hazard from THIS building that compounds with the "
            "above advisories or crosses the 25m overlap band toward adjacent sectors.\n"
            "  3. If overlap band is affected, explicitly name the peer scout to coordinate with."
        )
        return "\n".join(lines)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Module-level singleton — shared across all Scout instances in the process.
_shared_state = SharedState()


def get_shared_state() -> SharedState:
    return _shared_state
