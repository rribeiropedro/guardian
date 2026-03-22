'use client'

import { useCallback, useState } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import type {
  Building,
  Scout,
  ChatMessage,
  ServerMessage,
  Waypoint,
} from '../_lib/types'
import { useWebSocket } from '../_lib/useWebSocket'
import ScenarioInput from './ScenarioInput'
import ScoutPanel from './ScoutPanel'
import RouteWalkthrough from './RouteWalkthrough'
import LocationSearch from './LocationSearch'

// Mapbox uses browser APIs — must be dynamically imported with no SSR
const MapView = dynamic(() => import('./MapView'), { ssr: false })
const VT_CENTER = { lat: 37.2284, lng: -80.4234 }

export default function CommandCenter() {
  const router = useRouter()
  const [buildings, setBuildings] = useState<Building[]>([])
  const [scouts, setScouts] = useState<Map<string, Scout>>(new Map())
  const [activeScoutId, setActiveScoutId] = useState<string | null>(null)
  const [activeBuilding, setActiveBuilding] = useState<Building | null>(null)
  const [scenarioRunning, setScenarioRunning] = useState(false)
  const [route, setRoute] = useState<Waypoint[] | null>(null)
  const [mapCenter, setMapCenter] = useState<[number, number] | undefined>(undefined)
  const [scenarioCenter, setScenarioCenter] = useState<{ lat: number; lng: number }>(VT_CENTER)
  const [crossRefLog, setCrossRefLog] = useState<string[]>([])

  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      case 'triage_result': {
        setBuildings(msg.buildings)
        setScenarioRunning(false)
        break
      }

      case 'scout_deployed': {
        setScouts((prev) => {
          const next = new Map(prev)
          const existing = next.get(msg.scout_id)
          next.set(msg.scout_id, {
            scout_id: msg.scout_id,
            building_id: msg.building_id,
            building_name: msg.building_name,
            status: msg.status,
            messages: existing?.messages ?? [],
            viewpoint: existing?.viewpoint,
          })
          return next
        })
        // Auto-focus the first deployed scout
        setActiveScoutId((prev) => prev ?? msg.scout_id)
        break
      }

      case 'scout_report': {
        const chatMsg: ChatMessage = {
          role: 'scout',
          text: msg.narrative,
          image_b64: msg.annotated_image_b64,
          viewpoint: msg.viewpoint,
          analysis: msg.analysis,
          timestamp: Date.now(),
        }
        setScouts((prev) => {
          const next = new Map(prev)
          const scout = next.get(msg.scout_id)
          if (scout) {
            next.set(msg.scout_id, {
              ...scout,
              status: 'active',
              messages: [...scout.messages, chatMsg],
              viewpoint: msg.viewpoint,
            })
          }
          return next
        })
        break
      }

      case 'cross_reference': {
        const line = `SCOUT-${msg.from_scout.toUpperCase()} → SCOUT-${msg.to_scout.toUpperCase()}: ${msg.finding}`
        setCrossRefLog((prev) => [line, ...prev.slice(0, 19)])
        // Inject a system message into both scouts' chats
        const crossMsg = (): ChatMessage => ({
          role: 'scout',
          text: `[CROSS-REF from ${msg.from_scout.toUpperCase()}] ${msg.finding}\nImpact: ${msg.impact}${msg.resolution ? `\nResolution: ${msg.resolution}` : ''}`,
          timestamp: Date.now(),
        })
        setScouts((prev) => {
          const next = new Map(prev)
          const to = next.get(msg.to_scout)
          if (to) {
            next.set(msg.to_scout, { ...to, messages: [...to.messages, crossMsg()] })
          }
          return next
        })
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
    }
  }, [])

  const { status: wsStatus, send } = useWebSocket(handleMessage)

  const handleScenarioSubmit = useCallback(
    (prompt: string, radius_m: number) => {
      setBuildings([])
      setScouts(new Map())
      setActiveScoutId(null)
      setActiveBuilding(null)
      setRoute(null)
      setCrossRefLog([])
      setScenarioRunning(true)
      setMapCenter([scenarioCenter.lng, scenarioCenter.lat])
      send({ type: 'start_scenario', prompt, center: scenarioCenter, radius_m })
    },
    [scenarioCenter, send],
  )

  const handleBuildingClick = useCallback((building: Building) => {
    setActiveBuilding(building)
    // Use the selected building as the next scenario epicenter.
    setScenarioCenter({ lat: building.lat, lng: building.lng })
  }, [])

  const handleCommanderMessage = useCallback(
    (scoutId: string, message: string) => {
      // Optimistically add to chat
      const chatMsg: ChatMessage = { role: 'commander', text: message, timestamp: Date.now() }
      setScouts((prev) => {
        const next = new Map(prev)
        const scout = next.get(scoutId)
        if (scout) next.set(scoutId, { ...scout, messages: [...scout.messages, chatMsg] })
        return next
      })
      send({ type: 'commander_message', scout_id: scoutId, message })
    },
    [send],
  )

  const handleRequestRoute = useCallback(
    (buildingId: string) => {
      const start = mapCenter ? { lat: mapCenter[1], lng: mapCenter[0] } : undefined
      send({ type: 'request_route', building_id: buildingId, ...(start ? { start } : {}) })
    },
    [send, mapCenter],
  )

  const handleDeployScout = useCallback(
    (buildingId: string) => {
      send({ type: 'deploy_scout', building_id: buildingId })
    },
    [send],
  )

  const handleCloseScout = useCallback((scoutId: string) => {
    setScouts((prev) => {
      const next = new Map(prev)
      next.delete(scoutId)
      return next
    })
    setActiveScoutId((prev) => (prev === scoutId ? null : prev))
  }, [])

  const handleGoFirstPerson = useCallback(
    (building: Building) => {
      const params = new URLSearchParams({
        lat: String(building.lat),
        lng: String(building.lng),
        name: building.name || `Building ${building.id}`,
      })
      router.push(`/fly?${params.toString()}`)
    },
    [router],
  )

  const scoutList = Array.from(scouts.values())

  return (
    <div className="fixed inset-0 flex overflow-hidden bg-[#0a0a0f]">
      {/* ── Full-screen map ── */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, width: '100vw', height: '100vh' }}>
        <MapView
          center={mapCenter}
          buildings={buildings}
          activeBuilding={activeBuilding ?? undefined}
          onBuildingClick={handleBuildingClick}
        />
      </div>

      {/* ── Top-left HUD ── */}
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
        <div className="flex items-center gap-3 px-4 py-2 rounded-xl border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md pointer-events-none">
          <span className="text-sm font-mono font-bold tracking-widest text-white">AEGIS-NET</span>
          <span className="text-xs font-mono text-slate-500 border-l border-white/10 pl-3">
            INCIDENT COMMAND
          </span>
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
      </div>

      {/* ── Building detail popup ── */}
      {activeBuilding && (
        <BuildingPopup
          building={activeBuilding}
          onDeploy={() => { handleDeployScout(activeBuilding.id); setActiveBuilding(null) }}
          onGoFirstPerson={() => handleGoFirstPerson(activeBuilding)}
          onClose={() => setActiveBuilding(null)}
        />
      )}

      {/* ── Cross-reference feed (top-right, above scout panels) ── */}
      {crossRefLog.length > 0 && scoutList.length === 0 && (
        <CrossRefFeed log={crossRefLog} />
      )}

      {/* ── Scout panels (right rail) ── */}
      {scoutList.length > 0 && (
        <div className="absolute right-0 top-0 bottom-0 z-20 flex overflow-hidden">
          {scoutList.map((scout) => (
            <ScoutPanel
              key={scout.scout_id}
              scout={scout}
              isActive={scout.scout_id === activeScoutId}
              onFocus={() => setActiveScoutId(scout.scout_id)}
              onMessage={handleCommanderMessage}
              onRequestRoute={handleRequestRoute}
              onClose={() => handleCloseScout(scout.scout_id)}
            />
          ))}
        </div>
      )}

      {/* ── Route Walkthrough overlay ── */}
      {route && (
        <RouteWalkthrough
          waypoints={route}
          googleMapsApiKey={process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY}
          onClose={() => setRoute(null)}
        />
      )}

      {/* ── Scenario input ── */}
      <ScenarioInput
        wsStatus={wsStatus}
        onSubmit={handleScenarioSubmit}
        center={scenarioCenter}
        disabled={scenarioRunning}
      />
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

function BuildingPopup({
  building,
  onDeploy,
  onGoFirstPerson,
  onClose,
}: {
  building: Building
  onDeploy: () => void
  onGoFirstPerson: () => void
  onClose: () => void
}) {
  const COLOR_CLASSES: Record<string, string> = {
    RED: 'text-red-400 border-red-500/30 bg-red-500/10',
    ORANGE: 'text-orange-400 border-orange-500/30 bg-orange-500/10',
    YELLOW: 'text-yellow-400 border-yellow-500/30 bg-yellow-500/10',
    GREEN: 'text-green-400 border-green-500/30 bg-green-500/10',
  }

  return (
    <div className="absolute bottom-28 left-4 z-20 w-72 rounded-2xl border border-white/10 bg-[rgba(8,10,18,0.95)] backdrop-blur-md p-4 shadow-2xl">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-white">{building.name || `Building ${building.id}`}</h3>
          <span className={`inline-block mt-1 text-[10px] font-mono font-bold px-2 py-0.5 rounded border ${COLOR_CLASSES[building.color]}`}>
            {building.color} · Score {building.triage_score.toFixed(0)}
          </span>
        </div>
        <button onClick={onClose} className="text-slate-600 hover:text-slate-300 transition-colors">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="space-y-1 text-xs font-mono text-slate-400 mb-4">
        <div className="flex justify-between">
          <span>Damage probability</span>
          <span className="text-slate-200">{(building.damage_probability * 100).toFixed(0)}%</span>
        </div>
        <div className="flex justify-between">
          <span>Est. occupancy</span>
          <span className="text-slate-200">{building.estimated_occupancy}</span>
        </div>
        <div className="flex justify-between">
          <span>Material</span>
          <span className="text-slate-200">{building.material}</span>
        </div>
        <div className="flex justify-between">
          <span>Height</span>
          <span className="text-slate-200">{building.height_m.toFixed(0)} m</span>
        </div>
      </div>

      <div className="space-y-2">
        <button
          onClick={onDeploy}
          className="w-full py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-mono font-bold tracking-wider transition-colors"
        >
          DEPLOY SCOUT →
        </button>
        <button
          onClick={onGoFirstPerson}
          className="w-full py-2 rounded-xl border border-white/15 bg-white/5 hover:bg-white/10 text-slate-200 text-xs font-mono font-bold tracking-wider transition-colors"
        >
          GO FIRST PERSON
        </button>
      </div>
    </div>
  )
}

function CrossRefFeed({ log }: { log: string[] }) {
  return (
    <div className="absolute top-4 right-4 z-10 w-80 max-h-48 overflow-y-auto chat-scroll rounded-xl border border-yellow-500/20 bg-[rgba(8,10,18,0.85)] backdrop-blur-md p-3">
      <div className="text-[10px] font-mono text-yellow-400 font-bold tracking-wider mb-2">CROSS-REF FEED</div>
      {log.map((line, i) => (
        <div key={i} className="text-[10px] font-mono text-slate-400 leading-relaxed mb-1">
          {line}
        </div>
      ))}
    </div>
  )
}
