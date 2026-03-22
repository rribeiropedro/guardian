'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Waypoint } from '../_lib/types'

const HAZARD_COLORS = {
  blocked: '#ef4444',
  overhead: '#3B82F6',
  turn: '#22c55e',
  arrival: '#3b82f6',
  intel: '#a855f7',
  medical: '#ec4899',
}

const AUTO_ADVANCE_MS = 3000

interface Props {
  waypoints: Waypoint[]
  googleMapsApiKey?: string
  onClose: () => void
}

declare global {
  interface Window {
    google?: typeof google
    initStreetView?: () => void
  }
}

export default function RouteWalkthrough({ waypoints, googleMapsApiKey, onClose }: Props) {
  const panoramaRef = useRef<HTMLDivElement>(null)
  const svRef = useRef<google.maps.StreetViewPanorama | null>(null)
  const [currentIdx, setCurrentIdx] = useState(0)
  const [isPlaying, setIsPlaying] = useState(true)
  const [apiLoaded, setApiLoaded] = useState(false)
  const autoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load Google Maps JS API
  useEffect(() => {
    if (!googleMapsApiKey) {
      setApiLoaded(false)
      return
    }

    if (window.google?.maps?.StreetViewPanorama) {
      setApiLoaded(true)
      return
    }

    window.initStreetView = () => setApiLoaded(true)

    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${googleMapsApiKey}&callback=initStreetView`
    script.async = true
    document.head.appendChild(script)

    return () => {
      document.head.removeChild(script)
      delete window.initStreetView
    }
  }, [googleMapsApiKey])

  // Initialize StreetViewPanorama
  useEffect(() => {
    if (!apiLoaded || !panoramaRef.current || waypoints.length === 0) return

    const wp = waypoints[0]
    svRef.current = new window.google!.maps.StreetViewPanorama(panoramaRef.current, {
      pano: wp.pano_id,
      pov: { heading: wp.heading, pitch: 0 },
      zoom: 1,
      addressControl: false,
      showRoadLabels: false,
      linksControl: false,
      panControl: false,
      enableCloseButton: false,
      motionTracking: false,
    })
  }, [apiLoaded, waypoints])

  // Advance to waypoint
  const goToWaypoint = useCallback((idx: number) => {
    const wp = waypoints[idx]
    if (!wp || !svRef.current) return
    svRef.current.setPano(wp.pano_id)
    svRef.current.setPov({ heading: wp.heading, pitch: 0 })
    setCurrentIdx(idx)
  }, [waypoints])

  // Auto-advance timer
  useEffect(() => {
    if (!isPlaying || !apiLoaded) return
    autoTimerRef.current = setTimeout(() => {
      setCurrentIdx((prev) => {
        const next = prev + 1
        if (next >= waypoints.length) {
          setIsPlaying(false)
          return prev
        }
        goToWaypoint(next)
        return next
      })
    }, AUTO_ADVANCE_MS)
    return () => { if (autoTimerRef.current) clearTimeout(autoTimerRef.current) }
  }, [isPlaying, currentIdx, apiLoaded, waypoints.length, goToWaypoint])

  const handlePrev = () => {
    setIsPlaying(false)
    const next = Math.max(0, currentIdx - 1)
    goToWaypoint(next)
  }

  const handleNext = () => {
    setIsPlaying(false)
    const next = Math.min(waypoints.length - 1, currentIdx + 1)
    goToWaypoint(next)
  }

  const currentHazard = waypoints[currentIdx]?.hazard

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-black/90 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center gap-4 px-6 py-3 border-b border-white/10">
        <span className="text-xs font-mono font-bold tracking-widest text-blue-400">ROUTE WALKTHROUGH</span>
        <span className="text-xs font-mono text-slate-500">
          {currentIdx + 1} / {waypoints.length}
        </span>
        {currentHazard && (
          <span
            className="text-xs font-mono font-bold px-2 py-0.5 rounded"
            style={{ color: HAZARD_COLORS[currentHazard.type], border: `1px solid ${HAZARD_COLORS[currentHazard.type]}44` }}
          >
            {currentHazard.label}
          </span>
        )}
        <button
          onClick={onClose}
          className="ml-auto text-slate-400 hover:text-white transition-colors text-xs font-mono tracking-wider"
        >
          CLOSE ×
        </button>
      </div>

      {/* Street View panorama or fallback */}
      <div className="flex-1 relative">
        {googleMapsApiKey ? (
          <div ref={panoramaRef} className="absolute inset-0" />
        ) : (
          <NoPanoFallback waypoint={waypoints[currentIdx]} />
        )}

        {/* Hazard overlay */}
        {currentHazard && (
          <div
            className="absolute top-4 left-4 rounded-lg px-4 py-3 border font-mono"
            style={{
              background: `${HAZARD_COLORS[currentHazard.type]}22`,
              borderColor: `${HAZARD_COLORS[currentHazard.type]}66`,
              color: HAZARD_COLORS[currentHazard.type],
            }}
          >
            <div className="text-xs font-bold tracking-wider uppercase">{currentHazard.type}</div>
            <div className="text-sm mt-0.5">{currentHazard.label}</div>
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="border-t border-white/10 px-6 py-4">
        {/* Progress bar */}
        <div className="flex items-center gap-1 mb-4">
          {waypoints.map((wp, i) => (
            <button
              key={i}
              onClick={() => { setIsPlaying(false); goToWaypoint(i) }}
              className={`flex-1 h-1.5 rounded-full transition-colors ${i === currentIdx ? 'bg-blue-500' : i < currentIdx ? 'bg-blue-500/40' : 'bg-white/10'}`}
              title={wp.hazard?.label}
              style={wp.hazard ? { backgroundColor: i <= currentIdx ? HAZARD_COLORS[wp.hazard.type] : `${HAZARD_COLORS[wp.hazard.type]}44` } : undefined}
            />
          ))}
        </div>

        {/* Buttons */}
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={handlePrev}
            disabled={currentIdx === 0}
            className="p-2 rounded-lg border border-white/10 hover:border-white/20 disabled:opacity-30 transition-colors text-slate-300"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </button>

          <button
            onClick={() => setIsPlaying((p) => !p)}
            className="flex items-center gap-2 px-6 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors"
          >
            {isPlaying ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></svg>
                Pause
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M5 3l14 9-14 9V3z" /></svg>
                Play
              </>
            )}
          </button>

          <button
            onClick={handleNext}
            disabled={currentIdx === waypoints.length - 1}
            className="p-2 rounded-lg border border-white/10 hover:border-white/20 disabled:opacity-30 transition-colors text-slate-300"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}

function NoPanoFallback({ waypoint }: { waypoint?: Waypoint }) {
  if (!waypoint) return null
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-slate-500">
      <div className="text-4xl">🗺️</div>
      <div className="font-mono text-sm">Street View unavailable</div>
      <div className="font-mono text-xs text-slate-600">
        Set NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to enable walkthrough
      </div>
      <div className="mt-4 font-mono text-xs text-slate-600 space-y-1 text-center">
        <div>Pano ID: {waypoint.pano_id}</div>
        <div>Heading: {waypoint.heading.toFixed(1)}°</div>
        <div>Lat: {waypoint.lat.toFixed(5)}  Lng: {waypoint.lng.toFixed(5)}</div>
        {waypoint.hazard && (
          <div className="mt-2 text-blue-400">{waypoint.hazard.label}</div>
        )}
      </div>
    </div>
  )
}
