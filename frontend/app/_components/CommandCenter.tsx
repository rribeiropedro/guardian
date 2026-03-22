'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import type {
  Building,
  ServerMessage,
  Waypoint,
} from '../_lib/types'
import { useWebSocket } from '../_lib/useWebSocket'
import ScenarioInput from './ScenarioInput'
import RouteWalkthrough from './RouteWalkthrough'
import LocationSearch from './LocationSearch'

// Mapbox uses browser APIs — must be dynamically imported with no SSR
const MapView = dynamic(() => import('./MapView'), { ssr: false })
const VT_CENTER = { lat: 37.2284, lng: -80.4234 }

export default function CommandCenter() {
  const router = useRouter()
  const autoNavTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [buildings, setBuildings] = useState<Building[]>([])
  const [pinnedReds, setPinnedReds] = useState<Building[]>([])
  const [activeBuilding, setActiveBuilding] = useState<Building | null>(null)
  const [scenarioRunning, setScenarioRunning] = useState(false)
  const [route, setRoute] = useState<Waypoint[] | null>(null)
  const [mapCenter, setMapCenter] = useState<[number, number] | undefined>(undefined)
  const [scenarioCenter, setScenarioCenter] = useState<{ lat: number; lng: number }>(VT_CENTER)

  const clearAutoNavTimeout = useCallback(() => {
    if (autoNavTimeoutRef.current) {
      clearTimeout(autoNavTimeoutRef.current)
      autoNavTimeoutRef.current = null
    }
  }, [])

  const handleGoFirstPerson = useCallback(
    (building: Building, triageBuildings?: Building[]) => {
      try {
        // Persist triage colors/footprints so first-person view can render the same overlay.
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
        // Ignore storage failures (private mode/quota), fly view will fall back gracefully.
      }

      const params = new URLSearchParams({
        lat: String(building.lat),
        lng: String(building.lng),
        buildingId: building.id,
        name: building.name || `Building ${building.id}`,
      })
      router.push(`/fly?${params.toString()}`)
    },
    [buildings, router],
  )

  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      case 'triage_result': {
        setBuildings(msg.buildings)
        // Accumulate reds — they never get removed once seen
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

      case 'route_result': {
        setRoute(msg.waypoints)
        break
      }

      case 'error': {
        console.error('Backend error:', msg.message)
        break
      }
    }
  }, [clearAutoNavTimeout, handleGoFirstPerson])

  const { status: wsStatus, send } = useWebSocket(handleMessage)

  useEffect(() => {
    return () => {
      clearAutoNavTimeout()
    }
  }, [clearAutoNavTimeout])

  const handleScenarioSubmit = useCallback(
    (prompt: string, radius_m: number) => {
      clearAutoNavTimeout()
      // Keep red buildings visible during the loading transition
      setBuildings((prev) => prev.filter((b) => b.color === 'RED'))
      setActiveBuilding(null)
      setRoute(null)
      setScenarioRunning(true)
      setMapCenter([scenarioCenter.lng, scenarioCenter.lat])
      send({ type: 'start_scenario', prompt, center: scenarioCenter, radius_m })
    },
    [clearAutoNavTimeout, scenarioCenter, send],
  )

  const handleBuildingClick = useCallback((building: Building) => {
    clearAutoNavTimeout()
    setActiveBuilding(building)
    // Use the selected building as the next scenario epicenter.
    setScenarioCenter({ lat: building.lat, lng: building.lng })
  }, [clearAutoNavTimeout])

  const handleMapDoubleClick = useCallback((lat: number, lng: number) => {
    setScenarioCenter({ lat, lng })
  }, [])

  // Always include pinned reds even when buildings is cleared/updated
  const displayBuildings = useMemo(() => {
    const currentIds = new Set(buildings.map((b) => b.id))
    const orphanReds = pinnedReds.filter((r) => !currentIds.has(r.id))
    return orphanReds.length > 0 ? [...buildings, ...orphanReds] : buildings
  }, [buildings, pinnedReds])

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
        />
      </div>

      {/* ── Top-left HUD ── */}
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
        <div className="flex items-center gap-3 px-3 py-2 rounded-xl border border-orange-500/20 bg-[rgba(8,10,18,0.88)] backdrop-blur-md pointer-events-none" style={{boxShadow:'0 0 18px rgba(249,115,22,0.08)'}}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="GroundZero" className="h-7 w-7 rounded-md object-cover" style={{filter:'drop-shadow(0 0 6px rgba(249,115,22,0.5))'}} />
          <span className="text-sm font-mono font-bold tracking-widest text-white">GroundZero</span>
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

