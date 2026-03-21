"""Route calculation service — stub. Person C will provide real implementation."""
from __future__ import annotations

from ..models.schemas import ScoredBuilding, Waypoint


def calculate_route(
    start: tuple[float, float],
    target_building: ScoredBuilding,
    hazard_buildings: list[ScoredBuilding],
) -> list[Waypoint]:
    """Calculate a walking route from start to target_building, avoiding hazards.

    Args:
        start: (lat, lng) of the starting position.
        target_building: Destination building.
        hazard_buildings: Buildings to route around.

    Returns:
        Ordered list of Waypoints with Street View panorama IDs and hazard markers.
        Person C replaces this stub with real route calculation logic.
    """
    return []
