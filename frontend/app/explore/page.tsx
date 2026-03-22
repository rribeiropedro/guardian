'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'

declare global {
  interface Window {
    google?: typeof google
    initExploreStreetView?: () => void
  }
}

const API_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY

// Times Square — guaranteed Street View coverage
const DEFAULT_LAT = 40.7580
const DEFAULT_LNG = -73.9855

export default function ExplorePage() {
  const containerRef = useRef<HTMLDivElement>(null)
  const svRef = useRef<google.maps.StreetViewPanorama | null>(null)
  const svsRef = useRef<google.maps.StreetViewService | null>(null)
  const posRef = useRef<{ lat: number; lng: number }>({ lat: DEFAULT_LAT, lng: DEFAULT_LNG })
  const linksRef = useRef<google.maps.StreetViewLink[]>([])
  const [apiLoaded, setApiLoaded] = useState(
    () =>
      typeof window !== 'undefined' &&
      Boolean(window.google?.maps?.StreetViewPanorama),
  )
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [position, setPosition] = useState<{ lat: number; lng: number } | null>(null)

  // Load Google Maps API
  useEffect(() => {
    if (!API_KEY) return

    if (window.google?.maps?.StreetViewPanorama) return

    window.initExploreStreetView = () => setApiLoaded(true)

    const script = document.createElement('script')
    script.src = `https://maps.googleapis.com/maps/api/js?key=${API_KEY}&libraries=places&callback=initExploreStreetView`
    script.async = true
    document.head.appendChild(script)

    return () => {
      if (document.head.contains(script)) document.head.removeChild(script)
      delete window.initExploreStreetView
    }
  }, [])

  // Initialize Street View
  useEffect(() => {
    if (!apiLoaded || !containerRef.current) return

    svsRef.current = new window.google!.maps.StreetViewService()

    svRef.current = new window.google!.maps.StreetViewPanorama(containerRef.current, {
      position: { lat: DEFAULT_LAT, lng: DEFAULT_LNG },
      pov: { heading: 0, pitch: 0 },
      zoom: 1,
      addressControl: false,
      showRoadLabels: true,
      linksControl: true,
      panControl: true,
      enableCloseButton: false,
      motionTracking: false,
      fullscreenControl: false,
    })

    // Track position changes
    svRef.current.addListener('position_changed', () => {
      const pos = svRef.current?.getPosition()
      if (pos) {
        posRef.current = { lat: pos.lat(), lng: pos.lng() }
        setPosition({ lat: pos.lat(), lng: pos.lng() })
      }
    })

    // Cache links whenever pano changes
    svRef.current.addListener('pano_changed', () => {
      const links = svRef.current?.getLinks() ?? []
      linksRef.current = links.filter(
        (link): link is google.maps.StreetViewLink => link !== null,
      )
    })

    // WASD keyboard controls
    const handleKey = (e: KeyboardEvent) => {
      const sv = svRef.current
      if (!sv) return
      const pov = sv.getPov()

      if (e.key === 'a' || e.key === 'A') {
        sv.setPov({ heading: pov.heading - 10, pitch: pov.pitch })
      } else if (e.key === 'd' || e.key === 'D') {
        sv.setPov({ heading: pov.heading + 10, pitch: pov.pitch })
      } else if (e.key === 'w' || e.key === 'W' || e.key === 's' || e.key === 'S') {
        const isForward = e.key === 'w' || e.key === 'W'
        const targetHeading = isForward ? pov.heading : (pov.heading + 180) % 360
        const links = linksRef.current
        if (!links.length) return
        let best: google.maps.StreetViewLink | null = null
        let bestDiff = Infinity
        for (const link of links) {
          if (link?.heading == null) continue
          const diff = Math.abs(((link.heading - targetHeading + 540) % 360) - 180)
          if (diff < bestDiff) { bestDiff = diff; best = link }
        }
        if (best?.pano) sv.setPano(best.pano)
      }
    }

    window.addEventListener('keydown', handleKey)
    document.addEventListener('keydown', handleKey)

    // Re-attach to any iframes Street View creates internally
    const attachToIframes = () => {
      containerRef.current?.querySelectorAll('iframe').forEach((iframe) => {
        try {
          iframe.contentWindow?.addEventListener('keydown', handleKey)
        } catch {}
      })
    }
    const observer = new MutationObserver(attachToIframes)
    if (containerRef.current) {
      observer.observe(containerRef.current, { childList: true, subtree: true })
    }
    attachToIframes()

    return () => {
      window.removeEventListener('keydown', handleKey)
      document.removeEventListener('keydown', handleKey)
      observer.disconnect()
    }
  }, [apiLoaded])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim() || !svsRef.current || !window.google) return
    setError(null)

    // Use Places text search (included in the `places` library we already load)
    const service = new window.google.maps.places.PlacesService(
      document.createElement('div')
    )
    service.findPlaceFromQuery(
      { query, fields: ['geometry'] },
      (results, status) => {
        if (status !== 'OK' || !results?.[0]?.geometry?.location) {
          setError('Location not found')
          return
        }

        const loc = results[0].geometry.location

        svsRef.current!.getPanorama(
          { location: loc, radius: 1000 },
          (data, svStatus) => {
            if (svStatus === 'OK' && data?.location?.pano) {
              svRef.current?.setPano(data.location.pano)
              svRef.current?.setPov({ heading: 0, pitch: 0 })
              setError(null)
            } else {
              setError('No Street View at this location')
            }
          }
        )
      }
    )
  }

  return (
    <div className="fixed inset-0 bg-[#0a0a0f] flex flex-col">
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 z-10 flex items-center gap-3 px-4 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-slate-400 hover:text-white transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          COMMAND CENTER
        </Link>

        <form onSubmit={handleSearch} className="flex gap-2 flex-1 max-w-md">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search any location..."
            className="flex-1 px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-white placeholder-slate-600 outline-none focus:border-blue-500/50 transition-colors"
          />
          <button
            type="submit"
            className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-mono font-bold tracking-wider transition-colors"
          >
            GO
          </button>
        </form>

        {error && (
          <span className="text-xs font-mono text-red-400">{error}</span>
        )}

        {position && (
          <span className="ml-auto text-xs font-mono text-slate-600">
            {position.lat.toFixed(5)}, {position.lng.toFixed(5)}
          </span>
        )}
      </div>

      {/* Street View */}
      <div className="absolute inset-0">
        {!API_KEY ? (
          <div className="flex items-center justify-center h-full text-slate-500 font-mono text-sm">
            Set NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to enable Street View
          </div>
        ) : (
          <div ref={containerRef} className="w-full h-full" />
        )}
      </div>
    </div>
  )
}
