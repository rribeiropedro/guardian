"use client";

import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import type { Building, TriageColor } from "../_lib/types";

const TRIAGE_HEX: Record<TriageColor, string> = {
  RED: "#ff5d73",
  ORANGE: "#ffad42",
  YELLOW: "#ffe066",
  GREEN: "#4ade80",
};
const STANDARD_STYLE = "mapbox://styles/mapbox/standard";

// Virginia Tech campus default center
const DEFAULT_CENTER: [number, number] = [-80.4234, 37.2284];
const DEFAULT_ZOOM = 15.5;

interface Props {
  center?: [number, number];
  buildings: Building[];
  activeBuilding?: Building;
  onBuildingClick: (building: Building, point: { x: number; y: number }) => void;
  ensureSpaceForOverlay?: boolean;
  onMapDoubleClick?: (lat: number, lng: number) => void;
  epicenter?: [number, number]; // [lng, lat]
}

export default function MapView({
  center,
  buildings,
  activeBuilding,
  onBuildingClick,
  ensureSpaceForOverlay,
  onMapDoubleClick,
  epicenter,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const buildingsRef = useRef<Building[]>(buildings);
  buildingsRef.current = buildings;
  const onMapDoubleClickRef = useRef(onMapDoubleClick);
  onMapDoubleClickRef.current = onMapDoubleClick;

  useEffect(() => {
    if (!containerRef.current) return;
    if (mapRef.current) return; // already initialized

    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (!token) {
      console.error("NEXT_PUBLIC_MAPBOX_TOKEN is not set");
      return;
    }

    mapboxgl.accessToken = token;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: STANDARD_STYLE,
      center: center ?? DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      pitch: 50,
      bearing: -17.6,
      antialias: true,
      dragRotate: true,
      pitchWithRotate: true,
    });

    mapRef.current = map;

    map.on("load", () => {
      // Standard style with regular day/light basemap.
      if ("setConfigProperty" in map) {
        (
          map as mapboxgl.Map & {
            setConfigProperty?: (importId: string, configName: string, value: unknown) => void;
          }
        ).setConfigProperty?.("basemap", "lightPreset", "day");
      }

      // On-screen controls make rotate/pitch discoverable.
      map.addControl(
        new mapboxgl.NavigationControl({
          showCompass: true,
          showZoom: true,
          visualizePitch: true,
        }),
        "top-right",
      );
      map.dragRotate.enable();
      map.touchZoomRotate.enable();
      map.touchZoomRotate.enableRotation();

      const layers = map.getStyle().layers;
      let firstSymbolId: string | undefined;
      for (const layer of layers) {
        if (layer.type === "symbol") {
          firstSymbolId = layer.id;
          break;
        }
      }

      // ── Triage overlay source (empty until triage_result arrives) ────────────
      map.addSource("triage-buildings", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer(
        {
          id: "triage-buildings-3d",
          source: "triage-buildings",
          type: "fill-extrusion",
          minzoom: 13,
          paint: {
            // Keep geometry present for depth/clicking, but avoid full color wash.
            "fill-extrusion-color": "#334155",
            "fill-extrusion-height": ["get", "height_m"],
            "fill-extrusion-base": 0,
            "fill-extrusion-opacity": 0.28,
            "fill-extrusion-vertical-gradient": true,
          },
        },
        firstSymbolId,
      );

      map.addSource("triage-markers", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "triage-markers-glow",
        source: "triage-markers",
        type: "circle",
        paint: {
          "circle-color": ["get", "color_hex"],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 6, 18, 18],
          "circle-opacity": 0.2,
          "circle-blur": 0.75,
          "circle-stroke-width": 0,
        },
      });

      map.addLayer({
        id: "triage-markers-circle",
        source: "triage-markers",
        type: "circle",
        paint: {
          "circle-color": ["get", "color_hex"],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 2.75, 18, 8],
          "circle-opacity": 0.95,
          "circle-stroke-color": "#f8fafc",
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 13, 1.1, 18, 1.8],
        },
      });

      // ── Selected building highlight ───────────────────────────────────────────
      map.addSource("selected-building", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "selected-building-glow",
        source: "selected-building",
        type: "fill-extrusion",
        paint: {
          "fill-extrusion-color": "#60a5fa",
          "fill-extrusion-height": ["get", "height_m"],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.5,
          "fill-extrusion-vertical-gradient": true,
        },
      });

      // ── Epicenter pin (double-click to set) ──────────────────────────────────
      map.addSource("epicenter", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "epicenter-glow",
        source: "epicenter",
        type: "circle",
        paint: {
          "circle-color": "#ffffff",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 12, 18, 28],
          "circle-opacity": 0.12,
          "circle-blur": 0.9,
          "circle-stroke-width": 0,
        },
      });

      map.addLayer({
        id: "epicenter-dot",
        source: "epicenter",
        type: "circle",
        paint: {
          "circle-color": "#ffffff",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 3, 18, 6],
          "circle-opacity": 0.9,
          "circle-stroke-color": "#60a5fa",
          "circle-stroke-width": 2,
        },
      });

      map.doubleClickZoom.disable();
      map.on("dblclick", (e) => {
        onMapDoubleClickRef.current?.(e.lngLat.lat, e.lngLat.lng);
      });

      // ── Click handler (triage buildings) ─────────────────────────────────────
      map.on("click", "triage-buildings-3d", (e) => {
        if (!e.features?.[0]) return;
        const props = e.features[0].properties as { building_id: string };
        const building = buildingsRef.current.find(
          (b) => b.id === props.building_id,
        );
        if (building) onBuildingClick(building, { x: e.point.x, y: e.point.y });
      });
      map.on("click", "triage-markers-circle", (e) => {
        if (!e.features?.[0]) return;
        const props = e.features[0].properties as { building_id: string };
        const building = buildingsRef.current.find(
          (b) => b.id === props.building_id,
        );
        if (building) onBuildingClick(building, { x: e.point.x, y: e.point.y });
      });

      map.on("mouseenter", "triage-buildings-3d", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "triage-buildings-3d", () => {
        map.getCanvas().style.cursor = "";
      });
      map.on("mouseenter", "triage-markers-circle", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "triage-markers-circle", () => {
        map.getCanvas().style.cursor = "";
      });
      // Render initial buildings if already in state
      if (buildingsRef.current.length > 0) {
        updateSource(map, buildingsRef.current);
      }
    });

    return () => {
      // intentionally not removing the map on effect re-runs (React strict mode)
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update triage overlay when buildings change
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !map.getSource("triage-buildings"))
      return;
    updateSource(map, buildings);
  }, [buildings]);

  // Highlight the active building
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("selected-building") as
      | mapboxgl.GeoJSONSource
      | undefined;
    if (!source) return;
    source.setData({
      type: "FeatureCollection",
      features: activeBuilding ? [buildingToFeature(activeBuilding)] : [],
    });
  }, [activeBuilding]);

  // Update epicenter pin
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("epicenter") as mapboxgl.GeoJSONSource | undefined;
    if (!source) return;
    source.setData({
      type: "FeatureCollection",
      features: epicenter
        ? [{ type: "Feature", geometry: { type: "Point", coordinates: epicenter }, properties: {} }]
        : [],
    });
  }, [epicenter]);

  // Fly to new center
  useEffect(() => {
    if (!center || !mapRef.current) return;
    mapRef.current.flyTo({
      center,
      zoom: DEFAULT_ZOOM,
      pitch: 50,
      duration: 1800,
    });
  }, [center]);

  // If an overlay needs space near a clicked building, zoom out slightly.
  useEffect(() => {
    if (!ensureSpaceForOverlay || !mapRef.current) return;
    const map = mapRef.current;
    map.easeTo({
      zoom: Math.max(map.getZoom() - 0.8, 12),
      duration: 700,
    });
  }, [ensureSpaceForOverlay]);

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        width: "100%",
        height: "100%",
      }}
    />
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildingToFeature(b: Building): GeoJSON.Feature<GeoJSON.Polygon> {
  // Backend footprint is [lat, lng] pairs; GeoJSON wants [lng, lat]
  const coords = b.footprint.map(
    ([lat, lng]) => [lng, lat] as [number, number],
  );
  // Close the ring
  if (coords.length > 0) coords.push(coords[0]);

  return {
    type: "Feature",
    id: b.id,
    geometry: { type: "Polygon", coordinates: [coords] },
    properties: {
      building_id: b.id,
      name: b.name,
      color_hex: TRIAGE_HEX[b.color],
      height_m: Math.max(b.height_m, 4),
      triage_score: b.triage_score,
      damage_probability: b.damage_probability,
      estimated_occupancy: b.estimated_occupancy,
    },
  };
}

function updateSource(map: mapboxgl.Map, buildings: Building[]) {
  const polygonSource = map.getSource("triage-buildings") as
    | mapboxgl.GeoJSONSource
    | undefined;
  const markerSource = map.getSource("triage-markers") as
    | mapboxgl.GeoJSONSource
    | undefined;
  if (!polygonSource || !markerSource) return;

  polygonSource.setData({
    type: "FeatureCollection",
    features: buildings.map(buildingToFeature),
  });
  markerSource.setData({
    type: "FeatureCollection",
    features: buildings.map(buildingToMarkerFeature),
  });
}

function buildingToMarkerFeature(
  b: Building,
): GeoJSON.Feature<GeoJSON.Point> {
  return {
    type: "Feature",
    id: `${b.id}-marker`,
    geometry: { type: "Point", coordinates: [b.lng, b.lat] },
    properties: {
      building_id: b.id,
      color_hex: TRIAGE_HEX[b.color],
    },
  };
}
