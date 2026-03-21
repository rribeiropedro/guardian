'use client'

import { useEffect, useRef } from 'react'
import mapboxgl from 'mapbox-gl'
import 'mapbox-gl/dist/mapbox-gl.css'
import type { Building, TriageColor } from '../_lib/types'

const TRIAGE_HEX: Record<TriageColor, string> = {
  RED: '#ef4444',
  ORANGE: '#f97316',
  YELLOW: '#eab308',
  GREEN: '#22c55e',
}

// Virginia Tech campus default center
const DEFAULT_CENTER: [number, number] = [-80.4234, 37.2284]
const DEFAULT_ZOOM = 15.5

interface Props {
  center?: [number, number]
  buildings: Building[]
  activeBuildingId?: string
  onBuildingClick: (building: Building) => void
}

export default function MapView({ center, buildings, activeBuildingId, onBuildingClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<mapboxgl.Map | null>(null)
  const buildingsRef = useRef<Building[]>(buildings)
  buildingsRef.current = buildings

  useEffect(() => {
    if (!containerRef.current) return
    if (mapRef.current) return  // already initialized

    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN
    if (!token) {
      console.error('NEXT_PUBLIC_MAPBOX_TOKEN is not set')
      return
    }

    mapboxgl.accessToken = token

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: center ?? DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      pitch: 50,
      bearing: -17.6,
      antialias: true,
    })

    mapRef.current = map

    map.on('load', () => {
      // Add ambient + directional light for 3D buildings
      map.setLight({ anchor: 'viewport', color: 'white', intensity: 0.4 })

      // ── Base OSM 3D buildings ────────────────────────────────────────────────
      const layers = map.getStyle().layers
      // Find the first symbol layer to insert the extrusion below labels
      let firstSymbolId: string | undefined
      for (const layer of layers) {
        if (layer.type === 'symbol') {
          firstSymbolId = layer.id
          break
        }
      }

      map.addLayer(
        {
          id: 'base-buildings-3d',
          source: 'composite',
          'source-layer': 'building',
          filter: ['==', 'extrude', 'true'],
          type: 'fill-extrusion',
          minzoom: 14,
          paint: {
            'fill-extrusion-color': '#1e293b',
            'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], 14, 0, 14.05, ['get', 'height']],
            'fill-extrusion-base': ['interpolate', ['linear'], ['zoom'], 14, 0, 14.05, ['get', 'min_height']],
            'fill-extrusion-opacity': 0.7,
          },
        },
        firstSymbolId,
      )

      // ── Triage overlay source (empty until triage_result arrives) ────────────
      map.addSource('triage-buildings', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      })

      map.addLayer(
        {
          id: 'triage-buildings-3d',
          source: 'triage-buildings',
          type: 'fill-extrusion',
          minzoom: 13,
          paint: {
            'fill-extrusion-color': ['get', 'color_hex'],
            'fill-extrusion-height': ['get', 'height_m'],
            'fill-extrusion-base': 0,
            'fill-extrusion-opacity': 0.85,
          },
        },
        firstSymbolId,
      )

      // ── Click handler ────────────────────────────────────────────────────────
      map.on('click', 'triage-buildings-3d', (e) => {
        if (!e.features?.[0]) return
        const props = e.features[0].properties as { building_id: string }
        const building = buildingsRef.current.find((b) => b.id === props.building_id)
        if (building) onBuildingClick(building)
      })

      map.on('mouseenter', 'triage-buildings-3d', () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', 'triage-buildings-3d', () => {
        map.getCanvas().style.cursor = ''
      })

      // Render initial buildings if already in state
      if (buildingsRef.current.length > 0) {
        updateSource(map, buildingsRef.current)
      }
    })

    return () => {
      // intentionally not removing the map on effect re-runs (React strict mode)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Update triage overlay when buildings change
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded() || !map.getSource('triage-buildings')) return
    updateSource(map, buildings)
  }, [buildings])

  // Highlight the active building
  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    buildings.forEach((b) => {
      map.setFeatureState({ source: 'triage-buildings', id: b.id }, { active: b.id === activeBuildingId })
    })
  }, [activeBuildingId, buildings])

  // Fly to new center
  useEffect(() => {
    if (!center || !mapRef.current) return
    mapRef.current.flyTo({ center, zoom: DEFAULT_ZOOM, pitch: 50, duration: 1800 })
  }, [center])

  return (
    <div ref={containerRef} style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, width: '100%', height: '100%' }} />
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildingToFeature(b: Building): GeoJSON.Feature<GeoJSON.Polygon> {
  // Backend footprint is [lat, lng] pairs; GeoJSON wants [lng, lat]
  const coords = b.footprint.map(([lat, lng]) => [lng, lat] as [number, number])
  // Close the ring
  if (coords.length > 0) coords.push(coords[0])

  return {
    type: 'Feature',
    id: b.id,
    geometry: { type: 'Polygon', coordinates: [coords] },
    properties: {
      building_id: b.id,
      name: b.name,
      color_hex: TRIAGE_HEX[b.color],
      height_m: Math.max(b.height_m, 4),
      triage_score: b.triage_score,
      damage_probability: b.damage_probability,
      estimated_occupancy: b.estimated_occupancy,
    },
  }
}

function updateSource(map: mapboxgl.Map, buildings: Building[]) {
  const source = map.getSource('triage-buildings') as mapboxgl.GeoJSONSource | undefined
  if (!source) return
  source.setData({
    type: 'FeatureCollection',
    features: buildings.map(buildingToFeature),
  })
}
