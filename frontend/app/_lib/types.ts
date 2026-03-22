// TypeScript types matching backend/models/schemas.py exactly

export type TriageColor = 'RED' | 'ORANGE' | 'YELLOW' | 'GREEN'
export type Severity = 'CRITICAL' | 'MODERATE' | 'LOW'
export type CardinalFacing = 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW'

export interface Building {
  id: string
  name: string
  lat: number
  lng: number
  footprint: [number, number][]   // [lat, lng] pairs — swap to [lng, lat] for GeoJSON
  triage_score: number
  color: TriageColor
  damage_probability: number
  estimated_occupancy: number
  material: string
  height_m: number
}

export interface ScoutViewpoint {
  lat: number
  lng: number
  heading: number
  pitch: number
  facing: CardinalFacing
}

export interface Finding {
  category: 'structural' | 'access' | 'overhead' | 'route'
  description: string
  severity: Severity
  bbox?: number[]
}

export interface ScoutAnalysis {
  risk_level: Severity
  findings: Finding[]
  recommended_action: string
  approach_viable: boolean
}

export interface Hazard {
  type: 'blocked' | 'overhead' | 'turn' | 'arrival' | 'intel' | 'medical'
  color: string
  label: string
}

export interface Waypoint {
  lat: number
  lng: number
  heading: number
  pano_id: string
  hazard?: Hazard
}

// ── Server → Frontend messages ────────────────────────────────────────────────

export interface TriageResultMsg {
  type: 'triage_result'
  scenario_id: string
  buildings: Building[]
}

export interface ScoutDeployedMsg {
  type: 'scout_deployed'
  scout_id: string
  building_id: string
  building_name: string
  status: 'arriving' | 'active' | 'idle'
}

export interface ScoutReportMsg {
  type: 'scout_report'
  scout_id: string
  building_id: string
  viewpoint: ScoutViewpoint
  analysis: ScoutAnalysis
  annotated_image_b64: string
  narrative: string
}

export interface CrossReferenceMsg {
  type: 'cross_reference'
  from_scout: string
  to_scout: string
  finding: string
  impact: string
  resolution: string | null
}

export interface RouteResultMsg {
  type: 'route_result'
  target_building_id: string
  waypoints: Waypoint[]
  ghost_waypoints: Waypoint[]
  agent_validated: boolean
}

export interface AgentStreamStartMsg {
  type: 'agent_stream_start'
  scout_id: string
  building_id: string
}

export interface AgentStreamChunkMsg {
  type: 'agent_stream_chunk'
  scout_id: string
  building_id: string
  chunk: string
  sequence: number
}

export interface AgentStreamEndMsg {
  type: 'agent_stream_end'
  scout_id: string
  building_id: string
}

export interface ErrorMsg {
  type: 'error'
  message: string
}

export interface ScoutsConcludedMsg {
  type: 'scouts_concluded'
  /** Highest-priority building id — the auto-route target. */
  target_building_id: string
}

export type ServerMessage =
  | TriageResultMsg
  | ScoutDeployedMsg
  | ScoutReportMsg
  | CrossReferenceMsg
  | RouteResultMsg
  | AgentStreamStartMsg
  | AgentStreamChunkMsg
  | AgentStreamEndMsg
  | ErrorMsg
  | ScoutsConcludedMsg

// ── Frontend → Server messages ────────────────────────────────────────────────

export interface StartScenarioMsg {
  type: 'start_scenario'
  prompt: string
  center: { lat: number; lng: number }
  radius_m: number
}

export interface CommanderMessageMsg {
  type: 'commander_message'
  scout_id: string
  message: string
}

export interface DeployScoutMsg {
  type: 'deploy_scout'
  building_id: string
}

export interface RequestRouteMsg {
  type: 'request_route'
  building_id: string
  start?: { lat: number; lng: number }
}

export type ClientMessage =
  | StartScenarioMsg
  | CommanderMessageMsg
  | DeployScoutMsg
  | RequestRouteMsg

// ── Agent comms feed types ────────────────────────────────────────────────────

export type AgentFeedEntryType = 'status' | 'streaming' | 'sitrep' | 'cross_ref' | 'commander'

export interface AgentFeedEntry {
  id: string
  timestamp: number
  entryType: AgentFeedEntryType
  from: string
  to?: string
  text: string
  isStreaming?: boolean
  analysis?: ScoutAnalysis
}

// ── Frontend-only state types ─────────────────────────────────────────────────

export interface ChatMessage {
  role: 'commander' | 'scout'
  text: string
  image_b64?: string
  viewpoint?: ScoutViewpoint
  analysis?: ScoutAnalysis
  timestamp: number
}

export interface Scout {
  scout_id: string
  building_id: string
  building_name: string
  status: 'arriving' | 'active' | 'idle'
  messages: ChatMessage[]
  viewpoint?: ScoutViewpoint
}
