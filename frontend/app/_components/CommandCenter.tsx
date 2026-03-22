'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import type {
  AgentFeedEntry,
  Building,
  ChatMessage,
  Scout,
  ServerMessage,
  Waypoint,
} from '../_lib/types'
import { useWebSocket } from '../_lib/useWebSocket'
import ScenarioInput from './ScenarioInput'
import LocationSearch from './LocationSearch'
import FEMAReport from './FEMAReport'
import AgentCommsPanel from './AgentCommsPanel'

// Mapbox uses browser APIs — must be dynamically imported with no SSR
const MapView = dynamic(() => import('./MapView'), { ssr: false })
const VT_CENTER = { lat: 37.2284, lng: -80.4234 }

// Scout trail colors by arrival order — matches AgentCommsPanel callsign colors
const SCOUT_TRAIL_COLORS = ['#4ade80', '#60a5fa', '#a78bfa', '#f97316'] as const

export default function CommandCenter() {
  const autoNavTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flyHintTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [buildings, setBuildings] = useState<Building[]>([])
  const [pinnedReds, setPinnedReds] = useState<Building[]>([])
  const [activeBuilding, setActiveBuilding] = useState<Building | null>(null)
  const [scenarioRunning, setScenarioRunning] = useState(false)
  const [route, setRoute] = useState<Waypoint[] | null>(null)
  const [mapCenter, setMapCenter] = useState<[number, number] | undefined>(undefined)
  const [scenarioCenter, setScenarioCenter] = useState<{ lat: number; lng: number }>(VT_CENTER)
  const [flyMode, setFlyMode] = useState(false)
  const [flyTarget, setFlyTarget] = useState<Building | null>(null)
  const [flyHint, setFlyHint] = useState(false)
  // Set by the scouts_concluded handler; a useEffect below sends the route request
  // once `send` is available (send comes from useWebSocket, defined after handleMessage).
  const [pendingRouteBuildingId, setPendingRouteBuildingId] = useState<string | null>(null)
  // True only after scouts_concluded fires — gates the manual ROUTE button so it
  // only fires once SharedState has full hazard data from all scouts.
  const [scoutsHaveConcluded, setScoutsHaveConcluded] = useState(false)
  const [lastScenarioPrompt, setLastScenarioPrompt] = useState('')
  const [showFEMAReport, setShowFEMAReport] = useState(false)

  // ── Agent state ──────────────────────────────────────────────────────────────
  const [scouts, setScouts] = useState<Scout[]>([])
  const [agentFeed, setAgentFeed] = useState<AgentFeedEntry[]>([])
  // Maps scout_id → the feed entry ID for the active stream
  const streamEntryIds = useRef<Record<string, string>>({})

  // ── Scout trail tracking ──────────────────────────────────────────────────────
  // scoutOrder: scout IDs in the order they first appeared (determines color slot)
  const [scoutOrder, setScoutOrder] = useState<string[]>([])
  // scoutTrailPoints: scout_id → ordered list of visited viewpoint coordinates
  const [scoutTrailPoints, setScoutTrailPoints] = useState<Record<string, Array<{lat: number, lng: number}>>>({})

  const addFeedEntry = useCallback((entry: Omit<AgentFeedEntry, 'id' | 'timestamp'>) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    setAgentFeed(prev => [...prev, { ...entry, id, timestamp: Date.now() }])
    return id
  }, [])

  // ── Map nav helpers ───────────────────────────────────────────────────────────
  const clearAutoNavTimeout = useCallback(() => {
    if (autoNavTimeoutRef.current) {
      clearTimeout(autoNavTimeoutRef.current)
      autoNavTimeoutRef.current = null
    }
  }, [])

  const handleGoFirstPerson = useCallback(
    (building: Building, triageBuildings?: Building[]) => {
      try {
        const sourceBuildings = triageBuildings ?? buildings
        const triageSnapshot = sourceBuildings.map((b) => ({
          id: b.id,
          name: b.name,
          color: b.color,
          height_m: b.height_m,
          material: b.material,
          triage_score: b.triage_score,
          damage_probability: b.damage_probability,
          footprint: b.footprint,
        }))
        sessionStorage.setItem('aegis_triage_buildings', JSON.stringify(triageSnapshot))
      } catch {
        // Ignore storage failures (private mode/quota)
      }

      setActiveBuilding(building)
      setFlyTarget(building)
      setFlyMode(true)
      setFlyHint(true)
      if (flyHintTimeoutRef.current) clearTimeout(flyHintTimeoutRef.current)
      flyHintTimeoutRef.current = setTimeout(() => setFlyHint(false), 3000)
    },
    [buildings],
  )

  // ── WebSocket message handler ─────────────────────────────────────────────────
  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      // ── Map / triage ─────────────────────────────────────────────────────────
      case 'triage_result': {
        setBuildings(msg.buildings)
        setPinnedReds((prev) => {
          const existingIds = new Set(prev.map((b) => b.id))
          const newReds = msg.buildings.filter((b) => b.color === 'RED' && !existingIds.has(b.id))
          return newReds.length > 0 ? [...prev, ...newReds] : prev
        })
        setScenarioRunning(false)
        clearAutoNavTimeout()
        if (msg.buildings.length > 0) {
          const target = msg.buildings[0]
          setActiveBuilding(target)
          autoNavTimeoutRef.current = setTimeout(() => {
            handleGoFirstPerson(target, msg.buildings)
          }, 5000)
        }
        break
      }

      case 'scouts_concluded': {
        // All scouts have finished analysis + auto-survey — SharedState has the
        // richest hazard data possible.  Set pending building id; the useEffect
        // below fires the request_route once `send` is in scope.
        addFeedEntry({
          entryType: 'status',
          from: 'ICS',
          text: 'All scouts concluded. Calculating safe route to priority target…',
        })
        setScoutsHaveConcluded(true)
        setPendingRouteBuildingId(msg.target_building_id)
        break
      }

      case 'route_result': {
        setRoute(msg.waypoints)
        break
      }

      case 'error': {
        console.error('Backend error:', msg.message)
        break
      }

      // ── Scout lifecycle ───────────────────────────────────────────────────────
      case 'scout_deployed': {
        setScouts(prev => {
          const exists = prev.find(s => s.scout_id === msg.scout_id)
          if (exists) {
            return prev.map(s =>
              s.scout_id === msg.scout_id
                ? { ...s, status: msg.status, building_id: msg.building_id, building_name: msg.building_name }
                : s
            )
          }
          return [...prev, {
            scout_id: msg.scout_id,
            building_id: msg.building_id,
            building_name: msg.building_name,
            status: msg.status,
            messages: [],
          }]
        })
        // Register scout in color-slot order on first appearance
        setScoutOrder(prev => prev.includes(msg.scout_id) ? prev : [...prev, msg.scout_id])

        const statusText =
          msg.status === 'arriving'
            ? `Deploying to ${msg.building_name} — en route`
            : msg.status === 'active'
            ? `On station at ${msg.building_name} — initial assessment complete`
            : `Standing by at ${msg.building_name}`

        addFeedEntry({
          entryType: 'status',
          from: `SCOUT-${msg.scout_id.toUpperCase()}`,
          text: statusText,
        })
        break
      }

      // ── Streaming VLM output ──────────────────────────────────────────────────
      case 'agent_stream_start': {
        const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
        streamEntryIds.current[msg.scout_id] = id
        setAgentFeed(prev => [...prev, {
          id,
          timestamp: Date.now(),
          entryType: 'streaming',
          from: `SCOUT-${msg.scout_id.toUpperCase()}`,
          text: '',
          isStreaming: true,
        }])
        break
      }

      case 'agent_stream_chunk': {
        const entryId = streamEntryIds.current[msg.scout_id]
        if (entryId) {
          setAgentFeed(prev => prev.map(e =>
            e.id === entryId ? { ...e, text: e.text + msg.chunk } : e
          ))
        }
        break
      }

      case 'agent_stream_end': {
        const entryId = streamEntryIds.current[msg.scout_id]
        if (entryId) {
          setAgentFeed(prev => prev.map(e =>
            e.id === entryId ? { ...e, isStreaming: false, entryType: 'sitrep' } : e
          ))
          delete streamEntryIds.current[msg.scout_id]
        }
        break
      }

      // ── Full report (populates per-scout tab) ─────────────────────────────────
      case 'scout_report': {
        const chatMsg: ChatMessage = {
          role: 'scout',
          text: msg.narrative,
          image_b64: msg.annotated_image_b64,
          viewpoint: msg.viewpoint,
          analysis: msg.analysis,
          timestamp: Date.now(),
        }
        setScouts(prev => prev.map(s =>
          s.scout_id === msg.scout_id
            ? { ...s, messages: [...s.messages, chatMsg], viewpoint: msg.viewpoint }
            : s
        ))
        // Append viewpoint to this scout's trail
        setScoutTrailPoints(prev => ({
          ...prev,
          [msg.scout_id]: [...(prev[msg.scout_id] ?? []), { lat: msg.viewpoint.lat, lng: msg.viewpoint.lng }],
        }))
        break
      }

      // ── Cross-reference ───────────────────────────────────────────────────────
      case 'cross_reference': {
        addFeedEntry({
          entryType: 'cross_ref',
          from: `SCOUT-${msg.from_scout.toUpperCase()}`,
          to: `SCOUT-${msg.to_scout.toUpperCase()}`,
          text: msg.finding,
        })

        // Push into the receiving scout's detail view as well
        const crossMsg: ChatMessage = {
          role: 'scout',
          text: [
            `CROSS-REF from SCOUT-${msg.from_scout.toUpperCase()}`,
            msg.finding,
            msg.impact ? `\nIMPACT: ${msg.impact}` : '',
            msg.resolution ? `\nRESOLUTION: ${msg.resolution}` : '',
          ].filter(Boolean).join('\n'),
          timestamp: Date.now(),
        }
        setScouts(prev => prev.map(s =>
          s.scout_id === msg.to_scout
            ? { ...s, messages: [...s.messages, crossMsg] }
            : s
        ))
        break
      }
    }
  }, [clearAutoNavTimeout, handleGoFirstPerson, addFeedEntry])

  const { status: wsStatus, send } = useWebSocket(handleMessage)

  // Auto-send request_route once scouts_concluded fires and `send` is available.
  useEffect(() => {
    if (pendingRouteBuildingId) {
      send({ type: 'request_route', building_id: pendingRouteBuildingId })
      setPendingRouteBuildingId(null)
    }
  }, [pendingRouteBuildingId, send])

  useEffect(() => {
    return () => {
      clearAutoNavTimeout()
      if (flyHintTimeoutRef.current) {
        clearTimeout(flyHintTimeoutRef.current)
        flyHintTimeoutRef.current = null
      }
    }
  }, [clearAutoNavTimeout])

  // ── Commander message ─────────────────────────────────────────────────────────
  const handleCommanderMessage = useCallback((scoutId: string, message: string) => {
    send({ type: 'commander_message', scout_id: scoutId, message })

    // Echo immediately into the unified feed
    addFeedEntry({
      entryType: 'commander',
      from: 'CMD',
      to: `SCOUT-${scoutId.toUpperCase()}`,
      text: message,
    })

    // Echo into the scout's detail message list
    const cmdMsg: ChatMessage = {
      role: 'commander',
      text: message,
      timestamp: Date.now(),
    }
    setScouts(prev => prev.map(s =>
      s.scout_id === scoutId ? { ...s, messages: [...s.messages, cmdMsg] } : s
    ))
  }, [send, addFeedEntry])

  const handleRequestRoute = useCallback((buildingId: string) => {
    send({ type: 'request_route', building_id: buildingId })
  }, [send])

  const handleExitFly = useCallback(() => {
    setFlyMode(false)
    setFlyTarget(null)
    setFlyHint(false)
    if (flyHintTimeoutRef.current) {
      clearTimeout(flyHintTimeoutRef.current)
      flyHintTimeoutRef.current = null
    }
  }, [])

  // ── Scenario submit ───────────────────────────────────────────────────────────
  const handleScenarioSubmit = useCallback(
    (prompt: string, radius_m: number) => {
      clearAutoNavTimeout()
      setFlyMode(false)
      setFlyTarget(null)
      setFlyHint(false)
      setBuildings((prev) => prev.filter((b) => b.color === 'RED'))
      setActiveBuilding(null)
      setRoute(null)
      setPendingRouteBuildingId(null)
      setScenarioRunning(true)
      setScouts([])
      setAgentFeed([])
      streamEntryIds.current = {}
      setScoutOrder([])
      setScoutTrailPoints({})
      setLastScenarioPrompt(prompt)
      setShowFEMAReport(false)
      setScoutsHaveConcluded(false)
      setMapCenter([scenarioCenter.lng, scenarioCenter.lat])
      send({ type: 'start_scenario', prompt, center: scenarioCenter, radius_m })
    },
    [clearAutoNavTimeout, scenarioCenter, send],
  )

  const handleBuildingClick = useCallback((building: Building) => {
    clearAutoNavTimeout()
    setActiveBuilding(building)
    setScenarioCenter({ lat: building.lat, lng: building.lng })
  }, [clearAutoNavTimeout])

  const handleMapDoubleClick = useCallback((lat: number, lng: number) => {
    setScenarioCenter({ lat, lng })
  }, [])

  const displayBuildings = useMemo(() => {
    const currentIds = new Set(buildings.map((b) => b.id))
    const orphanReds = pinnedReds.filter((r) => !currentIds.has(r.id))
    return orphanReds.length > 0 ? [...buildings, ...orphanReds] : buildings
  }, [buildings, pinnedReds])

  // Stable array of trail data indexed by color slot — passed to MapView
  const scoutTrails = useMemo(() =>
    scoutOrder.map((id, i) => ({
      scoutId: id,
      color: SCOUT_TRAIL_COLORS[i] ?? '#94a3b8',
      points: scoutTrailPoints[id] ?? [],
    })),
    [scoutOrder, scoutTrailPoints],
  )

  return (
    <div className="fixed inset-0 flex overflow-hidden bg-[#0a0a0f]">
      {/* ── Full-screen map ── */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, width: '100vw', height: '100vh' }}>
        <MapView
          center={mapCenter}
          buildings={displayBuildings}
          pinnedReds={pinnedReds}
          activeBuilding={activeBuilding ?? undefined}
          onBuildingClick={handleBuildingClick}
          onMapDoubleClick={handleMapDoubleClick}
          epicenter={[scenarioCenter.lng, scenarioCenter.lat]}
          flyMode={flyMode}
          flyTarget={flyTarget ? { lat: flyTarget.lat, lng: flyTarget.lng, buildingId: flyTarget.id, name: flyTarget.name } : undefined}
          flyRoute={route}
          onFlyExit={handleExitFly}
          scoutTrails={scoutTrails}
        />
      </div>
      <style>{`.aegis-stop-popup .mapboxgl-popup-content{background:transparent;padding:0;box-shadow:none}.aegis-stop-popup .mapboxgl-popup-tip{display:none}`}</style>

      {/* ── Top-left HUD ── */}
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
        <div className="flex items-center gap-3 px-3 py-2 rounded-xl border border-blue-500/20 bg-[rgba(8,10,18,0.88)] backdrop-blur-md pointer-events-none" style={{boxShadow:'0 0 18px rgba(59,130,246,0.08)'}}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/helmet-logo.svg" alt="GroundZero" className="h-7 w-7 object-contain" style={{filter:'drop-shadow(0 0 6px rgba(59,130,246,0.5))'}} />
          <span className="text-sm font-mono font-bold tracking-widest text-white">GroundZero</span>
        </div>

        <LocationSearch
          onSelect={(center) => {
            setMapCenter(center)
            setScenarioCenter({ lat: center[1], lng: center[0] })
          }}
        />

        {scenarioRunning && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 backdrop-blur-md pointer-events-none">
            <span className="h-1.5 w-1.5 rounded-full bg-blue-400 arriving-pulse" />
            <span className="text-xs font-mono text-blue-400">Analyzing buildings…</span>
          </div>
        )}

        {buildings.length > 0 && (
          <BuildingSummary buildings={buildings} />
        )}

        {route !== null && (
          <button
            onClick={() => setShowFEMAReport(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 backdrop-blur-md text-xs font-mono text-blue-400 hover:text-blue-300 hover:border-blue-400/40 transition-colors"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            FEMA Report
          </button>
        )}

        {buildings.length > 0 && !scenarioRunning && !flyMode && (
          <button
            onClick={() => {
              setBuildings([])
              setPinnedReds([])
              setActiveBuilding(null)
              setRoute(null)
              setScouts([])
              setAgentFeed([])
              setScoutOrder([])
              setScoutTrailPoints({})
              setPendingRouteBuildingId(null)
              setScoutsHaveConcluded(false)
              streamEntryIds.current = {}
            }}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-slate-400 hover:text-slate-200 hover:border-white/20 transition-colors"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
            </svg>
            New Scenario
          </button>
        )}
      </div>

      {flyMode && (
        <div className="absolute top-4 right-4 z-20 flex flex-col items-end gap-2">
          <button
            onClick={handleExitFly}
            className="px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-slate-200 hover:text-white hover:border-white/30 transition-colors"
          >
            EXIT FLY MODE
          </button>
        </div>
      )}

      {flyMode && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="w-5 h-5 relative opacity-60">
            <div className="absolute top-0 bottom-0 left-1/2 w-px bg-white" />
            <div className="absolute left-0 right-0 top-1/2 h-px bg-white" />
          </div>
        </div>
      )}

      {flyHint && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 px-4 py-2 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.9)] backdrop-blur-md text-xs font-mono text-slate-400 text-center">
          Drag to look - WASD move - Space up - Shift down - Click to lock mouse - Esc exit
        </div>
      )}
      {flyMode && !flyHint && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 text-[10px] font-mono text-slate-700 pointer-events-none">
          ESC to exit
        </div>
      )}

      {/* ── Scout trail legend ── */}
      {scoutOrder.length > 0 && !flyMode && (
        <div className="absolute bottom-24 left-4 z-10 px-3 py-2 rounded-xl border border-white/[0.07] bg-[rgba(8,10,18,0.85)] backdrop-blur-md pointer-events-none">
          <div className="text-[9px] font-mono font-bold tracking-widest text-slate-600 uppercase mb-1.5">
            Scout Trails
          </div>
          <div className="flex flex-col gap-1">
            {scoutOrder.map((id, i) => (
              <div key={id} className="flex items-center gap-2">
                <svg width="16" height="8" viewBox="0 0 16 8">
                  <line x1="0" y1="4" x2="16" y2="4" stroke={SCOUT_TRAIL_COLORS[i] ?? '#94a3b8'} strokeWidth="1.5" strokeDasharray="3 1.5" />
                </svg>
                <span
                  className="h-2 w-2 rounded-full shrink-0"
                  style={{ backgroundColor: SCOUT_TRAIL_COLORS[i] ?? '#94a3b8' }}
                />
                <span className="text-[10px] font-mono" style={{ color: SCOUT_TRAIL_COLORS[i] ?? '#94a3b8' }}>
                  SCOUT-{id.toUpperCase()}
                </span>
                <span className="text-[9px] font-mono text-slate-600">
                  {(scoutTrailPoints[id]?.length ?? 0)} pts
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Scenario input — hidden once a scenario is active or in fly mode ── */}
      {!flyMode && !scenarioRunning && buildings.length === 0 && (
        <ScenarioInput
          wsStatus={wsStatus}
          onSubmit={handleScenarioSubmit}
          center={scenarioCenter}
          disabled={false}
        />
      )}

      {/* ── Agent Comms Panel ── */}
      <AgentCommsPanel
        scouts={scouts}
        feed={agentFeed}
        onMessage={handleCommanderMessage}
        onRequestRoute={handleRequestRoute}
        routeReady={scoutsHaveConcluded}
      />

      {/* ── FEMA Report overlay ── */}
      {showFEMAReport && (
        <FEMAReport
          buildings={buildings}
          scouts={scouts}
          route={route ?? []}
          feed={agentFeed}
          scenarioPrompt={lastScenarioPrompt}
          epicenterLat={scenarioCenter.lat}
          epicenterLng={scenarioCenter.lng}
          onClose={() => setShowFEMAReport(false)}
        />
      )}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

const COLOR_DOT: Record<string, string> = {
  RED: 'bg-red-500',
  ORANGE: 'bg-orange-500',
  YELLOW: 'bg-yellow-500',
  GREEN: 'bg-green-500',
}

function BuildingSummary({ buildings }: { buildings: Building[] }) {
  const counts = buildings.reduce<Record<string, number>>((acc, b) => {
    acc[b.color] = (acc[b.color] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-xl border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md pointer-events-none">
      {(['RED', 'ORANGE', 'YELLOW', 'GREEN'] as const).map((c) =>
        counts[c] ? (
          <span key={c} className="flex items-center gap-1.5 text-xs font-mono text-slate-400">
            <span className={`h-2 w-2 rounded-full ${COLOR_DOT[c]}`} />
            {counts[c]}
          </span>
        ) : null,
      )}
      <span className="text-xs font-mono text-slate-600 border-l border-white/10 pl-2">
        {buildings.length} buildings
      </span>
    </div>
  )
}
