from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MessageBase(BaseModel):
    type: str


class LatLng(BaseModel):
    lat: float
    lng: float


# ----------------------------
# OSM / Triage data models
# ----------------------------
class BuildingData(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    footprint: list[list[float]]  # list of [lat, lng] pairs
    material: str = "unknown"
    levels: int = 2
    height_m: float = 6.0
    building_type: str = "yes"
    start_date: str = ""  # OSM start_date / construction_date tag — used for age scoring


# Finding is needed by both VLMAnalysis and ScoutAnalysis — define it here
class Finding(BaseModel):
    category: Literal["structural", "access", "overhead", "route"]
    description: str
    severity: Literal["CRITICAL", "MODERATE", "LOW"]
    bbox: list[float] | None = None


# ----------------------------
# Triage / Scout internal models
# ----------------------------
class ScoredBuilding(BuildingData):
    triage_score: float = Field(ge=0, le=100)
    color: Literal["RED", "ORANGE", "YELLOW", "GREEN"]
    damage_probability: float = Field(ge=0.0, le=1.0)
    estimated_occupancy: int


class ExternalRisk(BaseModel):
    direction: str
    type: str
    estimated_range_m: float


class VLMAnalysis(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    risk_level: Literal["CRITICAL", "MODERATE", "LOW"] = "MODERATE"
    recommended_action: str = ""
    approach_viable: bool = True
    external_risks: list[ExternalRisk] = Field(default_factory=list)


# ----------------------------
# Server -> Frontend messages
# ----------------------------
class Building(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    footprint: list[list[float]]
    triage_score: float = Field(ge=0, le=100)
    color: Literal["RED", "ORANGE", "YELLOW", "GREEN"]
    damage_probability: float = Field(ge=0, le=1)
    estimated_occupancy: int
    material: str
    height_m: float


class TriageResult(MessageBase):
    type: Literal["triage_result"] = "triage_result"
    scenario_id: str
    buildings: list[Building]


class ScoutDeployed(MessageBase):
    type: Literal["scout_deployed"] = "scout_deployed"
    scout_id: str
    building_id: str
    building_name: str
    status: Literal["arriving", "active", "idle"]


class ScoutViewpoint(BaseModel):
    lat: float
    lng: float
    heading: float = Field(ge=0, le=360)
    pitch: float
    facing: Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


class ScoutAnalysis(BaseModel):
    risk_level: Literal["CRITICAL", "MODERATE", "LOW"]
    findings: list[Finding]
    recommended_action: str
    approach_viable: bool


class ScoutReport(MessageBase):
    type: Literal["scout_report"] = "scout_report"
    scout_id: str
    building_id: str
    viewpoint: ScoutViewpoint
    analysis: ScoutAnalysis
    annotated_image_b64: str
    narrative: str


class CrossReference(MessageBase):
    type: Literal["cross_reference"] = "cross_reference"
    from_scout: str
    to_scout: str
    finding: str
    impact: str
    resolution: str | None = None


class Hazard(BaseModel):
    type: Literal["blocked", "overhead", "turn", "arrival", "intel", "medical"]
    color: str
    label: str


class Waypoint(BaseModel):
    lat: float
    lng: float
    heading: float
    pano_id: str
    hazard: Hazard | None = None


class RouteResult(MessageBase):
    type: Literal["route_result"] = "route_result"
    target_building_id: str
    waypoints: list[Waypoint]
    # Ghost route: the shortest/most-direct path a normal navigation app would
    # suggest, computed WITHOUT hazard avoidance.  Hazard annotations on each
    # ghost waypoint show exactly WHY that path is dangerous post-earthquake.
    # Empty until the background route agent completes its analysis.
    ghost_waypoints: list[Waypoint] = []
    # True once the OpenClaw cloud route agent has validated / refined the waypoints.
    # The frontend can show a "route finalized" indicator when this flips.
    agent_validated: bool = False


class AgentStreamStart(MessageBase):
    type: Literal["agent_stream_start"] = "agent_stream_start"
    scout_id: str
    building_id: str


class AgentStreamChunk(MessageBase):
    type: Literal["agent_stream_chunk"] = "agent_stream_chunk"
    scout_id: str
    building_id: str
    chunk: str
    sequence: int


class AgentStreamEnd(MessageBase):
    type: Literal["agent_stream_end"] = "agent_stream_end"
    scout_id: str
    building_id: str


class ErrorMessage(MessageBase):
    type: Literal["error"] = "error"
    message: str


class ScoutsConcluded(MessageBase):
    """Emitted by the coordinator once all auto-deployed scouts have finished
    their arrival analysis AND background auto-survey.  The frontend should
    use this as the trigger to automatically request the route walkthrough for
    the highest-priority building — ensuring the route agent has the richest
    possible hazard data from all scout findings baked into SharedState."""
    type: Literal["scouts_concluded"] = "scouts_concluded"
    target_building_id: str  # highest-priority building — auto-route target


class FemaReport(MessageBase):
    """Full incident report exported on request. Contains all scenario data,
    triage scores, scout findings, and the final route for FEMA/ICS handoff."""
    type: Literal["fema_report"] = "fema_report"
    scenario_id: str
    generated_at: str
    scenario: dict          # prompt, epicenter, magnitude, time_of_day
    buildings: list[dict]   # all scored buildings
    scout_findings: list[dict]  # SharedState risk records
    route: dict | None      # last emitted route_result (None if not yet requested)
    waypoint_budget: dict   # used / total


# ----------------------------
# Frontend -> Server messages
# ----------------------------
class StartScenario(MessageBase):
    type: Literal["start_scenario"] = "start_scenario"
    prompt: str
    center: LatLng
    radius_m: float


class CommanderMessage(MessageBase):
    type: Literal["commander_message"] = "commander_message"
    scout_id: str
    message: str


class DeployScout(MessageBase):
    type: Literal["deploy_scout"] = "deploy_scout"
    building_id: str
    prompt: str | None = None


class RequestRoute(MessageBase):
    type: Literal["request_route"] = "request_route"
    building_id: str
    start: LatLng | None = None


FrontendMessage = StartScenario | CommanderMessage | DeployScout | RequestRoute
ServerMessage = (
    TriageResult
    | ScoutDeployed
    | ScoutReport
    | CrossReference
    | RouteResult
    | AgentStreamStart
    | AgentStreamChunk
    | AgentStreamEnd
    | ErrorMessage
    | ScoutsConcluded
    | FemaReport
)
