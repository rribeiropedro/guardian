"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import type { Building, TriageColor, Waypoint } from "../_lib/types";

const TRIAGE_HEX: Record<TriageColor, string> = {
  RED: "#ff5d73",
  ORANGE: "#ffad42",
  YELLOW: "#ffe066",
  GREEN: "#4ade80",
};

// Colors assigned to each scout trail slot (by arrival order)
const TRAIL_COLORS = ["#4ade80", "#60a5fa", "#a78bfa", "#f97316"] as const;
const MAX_SCOUT_TRAILS = 4;

export interface ScoutTrail {
  scoutId: string;
  color: string;
  points: Array<{ lat: number; lng: number }>;
}
const STANDARD_STYLE = "mapbox://styles/mapbox/standard";

// Virginia Tech campus default center
const DEFAULT_CENTER: [number, number] = [-80.4234, 37.2284];
const DEFAULT_ZOOM = 15.5;
const FLY_START_ALT = 90;
const FLY_START_BEARING = 95;
const FLY_START_PITCH = 80;
const FLY_SPEED = 14;
const FLY_SPRINT_MULT = 2.5;
const FLY_SENSITIVITY = 0.003;
const FLY_PITCH_MIN = -85;
const FLY_PITCH_MAX = 85;
const FLY_VERT_SPEED = 8;
const FLY_DAMPING = 0.88;

interface FlyTarget {
  lat: number;
  lng: number;
  buildingId?: string;
  name?: string;
}

interface FlyState {
  lng: number;
  lat: number;
  alt: number;
  bearing: number;
  pitch: number;
  velLng: number;
  velLat: number;
  velAlt: number;
  keys: Set<string>;
  active: boolean;
  lastTime: number;
}

interface Props {
  center?: [number, number];
  buildings: Building[];
  pinnedReds?: Building[];
  activeBuilding?: Building;
  onBuildingClick: (building: Building, point: { x: number; y: number }) => void;
  ensureSpaceForOverlay?: boolean;
  onMapDoubleClick?: (lat: number, lng: number) => void;
  epicenter?: [number, number]; // [lng, lat]
  flyMode?: boolean;
  flyTarget?: FlyTarget;
  flyRoute?: Waypoint[] | null;
  onFlyExit?: () => void;
  scoutTrails?: ScoutTrail[];
}

export default function MapView({
  center,
  buildings,
  pinnedReds = [],
  activeBuilding,
  onBuildingClick,
  ensureSpaceForOverlay,
  onMapDoubleClick,
  epicenter,
  flyMode = false,
  flyTarget,
  flyRoute,
  onFlyExit,
  scoutTrails,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const buildingsRef = useRef<Building[]>(buildings);
  buildingsRef.current = buildings;
  const onMapDoubleClickRef = useRef(onMapDoubleClick);
  onMapDoubleClickRef.current = onMapDoubleClick;
  const [mapLoaded, setMapLoaded] = useState(false);
  const flyModeRef = useRef(false);
  flyModeRef.current = flyMode;
  const rafRef = useRef<number | null>(null);
  const flyStateRef = useRef<FlyState>({
    lng: center?.[0] ?? DEFAULT_CENTER[0],
    lat: center?.[1] ?? DEFAULT_CENTER[1],
    alt: FLY_START_ALT,
    bearing: FLY_START_BEARING,
    pitch: FLY_START_PITCH,
    velLng: 0,
    velLat: 0,
    velAlt: 0,
    keys: new Set(),
    active: false,
    lastTime: 0,
  });
  const lastOrbitRef = useRef<{
    center: [number, number];
    zoom: number;
    pitch: number;
    bearing: number;
  } | null>(null);
  const lastFlyTargetRef = useRef<string | null>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPopupRef = useRef<mapboxgl.Popup | null>(null);

  // ── Scout trail animation state ───────────────────────────────────────────
  // Mirror of the scoutTrails prop — readable from RAF callbacks without stale closures.
  const scoutTrailsRef = useRef(scoutTrails);
  scoutTrailsRef.current = scoutTrails;

  interface SlotAnim {
    fromPt: { lat: number; lng: number };
    toPt: { lat: number; lng: number };
    startTime: number;
    duration: number;
  }
  // Per slot (0–2): active tween from old terminal point to new terminal point.
  const slotAnimRef = useRef<Map<number, SlotAnim>>(new Map());
  // Per slot: the last point that has been fully committed (animation finished).
  const committedLastPtRef = useRef<Map<number, { lat: number; lng: number }>>(new Map());
  // RAF handle for the trail animation loop (separate from the fly-mode RAF).
  const trailAnimRafRef = useRef<number | null>(null);

  // Smooth ease in-out for scout movement animation.
  const easeInOutCubic = (t: number) =>
    t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

  // Per-frame renderer for scout trail animations.
  // Stable (no deps — reads only refs) so it can be passed to requestAnimationFrame.
  const runTrailAnim = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;

    const now = performance.now();
    let anyActive = false;

    for (let i = 0; i < MAX_SCOUT_TRAILS; i++) {
      const src = map.getSource(`scout-trail-${i}`) as mapboxgl.GeoJSONSource | undefined;
      if (!src) continue;

      const trail = scoutTrailsRef.current?.[i];
      if (!trail || trail.points.length === 0) {
        src.setData({ type: "FeatureCollection", features: [] });
        continue;
      }

      const { points } = trail;
      const anim = slotAnimRef.current.get(i);
      let currentPt: { lat: number; lng: number };

      if (anim) {
        const t = Math.min((now - anim.startTime) / anim.duration, 1);
        const eased = easeInOutCubic(t);
        currentPt = {
          lat: anim.fromPt.lat + (anim.toPt.lat - anim.fromPt.lat) * eased,
          lng: anim.fromPt.lng + (anim.toPt.lng - anim.fromPt.lng) * eased,
        };
        if (t >= 1) {
          // Snap to final, mark committed, clear anim entry.
          currentPt = anim.toPt;
          committedLastPtRef.current.set(i, anim.toPt);
          slotAnimRef.current.delete(i);
        } else {
          anyActive = true;
        }
      } else {
        currentPt = points[points.length - 1];
      }

      // All confirmed points + the (possibly mid-tween) current position.
      const confirmedPoints = anim ? points.slice(0, -1) : points;
      const allPoints = anim ? [...confirmedPoints, currentPt] : points;

      const features: GeoJSON.Feature[] = [];
      if (allPoints.length >= 2) {
        features.push({
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: allPoints.map((p) => [p.lng, p.lat]),
          },
          properties: {},
        });
      }
      allPoints.forEach((p, j) => {
        features.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: [p.lng, p.lat] },
          properties: { isCurrent: j === allPoints.length - 1 },
        });
      });

      src.setData({ type: "FeatureCollection", features });
    }

    if (anyActive) {
      trailAnimRafRef.current = requestAnimationFrame(runTrailAnim);
    } else {
      trailAnimRafRef.current = null;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startFlyLoop = useCallback(() => {
    const step = (now: number) => {
      const s = flyStateRef.current;
      const map = mapRef.current;
      if (!s.active || !map) return;
      const dt = Math.min((now - s.lastTime) / 1000, 0.05);
      s.lastTime = now;

      const sprint = s.keys.has("shift");
      const speed = FLY_SPEED * (sprint ? FLY_SPRINT_MULT : 1);
      const bearRad = (s.bearing * Math.PI) / 180;

      let moveX = 0;
      let moveY = 0;
      if (s.keys.has("w") || s.keys.has("arrowup")) {
        moveX += Math.sin(bearRad);
        moveY += Math.cos(bearRad);
      }
      if (s.keys.has("s") || s.keys.has("arrowdown")) {
        moveX -= Math.sin(bearRad);
        moveY -= Math.cos(bearRad);
      }
      if (s.keys.has("a") || s.keys.has("arrowleft")) {
        moveX -= Math.cos(bearRad);
        moveY += Math.sin(bearRad);
      }
      if (s.keys.has("d") || s.keys.has("arrowright")) {
        moveX += Math.cos(bearRad);
        moveY -= Math.cos(bearRad);
      }

      const mag = Math.sqrt(moveX * moveX + moveY * moveY);
      if (mag > 0) {
        moveX /= mag;
        moveY /= mag;
      }

      const mPerDegLat = 111111;
      const mPerDegLng = 111111 * Math.cos((s.lat * Math.PI) / 180);
      s.velLng = s.velLng * FLY_DAMPING + (moveX * speed * dt) / mPerDegLng;
      s.velLat = s.velLat * FLY_DAMPING + (moveY * speed * dt) / mPerDegLat;
      s.lng += s.velLng;
      s.lat += s.velLat;

      let vert = 0;
      if (s.keys.has(" ") || s.keys.has("e")) vert = 1;
      if (s.keys.has("shift") || s.keys.has("q")) vert = -1;
      s.velAlt = s.velAlt * FLY_DAMPING + vert * FLY_VERT_SPEED * dt;
      s.alt = Math.max(2, s.alt + s.velAlt);

      const camera = map.getFreeCameraOptions();
      camera.position = mapboxgl.MercatorCoordinate.fromLngLat(
        { lng: s.lng, lat: s.lat },
        s.alt,
      );
      camera.setPitchBearing(s.pitch, s.bearing);
      map.setFreeCameraOptions(camera);
      map.triggerRepaint();

      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
  }, []);

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
            "fill-extrusion-color": ["get", "color_hex"],
            "fill-extrusion-height": ["+", ["get", "height_m"], 25],
            "fill-extrusion-base": ["get", "height_m"],
            "fill-extrusion-opacity": 0.85,
            "fill-extrusion-vertical-gradient": false,
          },
        },
        firstSymbolId,
      );

      // Keep triage-markers source for click interaction, but invisible
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
          "circle-radius": 0,
          "circle-opacity": 0,
        },
      });

      map.addLayer({
        id: "triage-markers-circle",
        source: "triage-markers",
        type: "circle",
        paint: {
          "circle-color": ["get", "color_hex"],
          "circle-radius": 8,
          "circle-opacity": 0,
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

      // ── Pinned reds — always visible, never cleared ───────────────────────────
      map.addSource("pinned-reds", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "pinned-reds-glow",
        source: "pinned-reds",
        type: "circle",
        paint: {
          "circle-color": "#ff1a1a",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 8, 18, 22],
          "circle-opacity": 0.25,
          "circle-blur": 0.7,
          "circle-stroke-width": 0,
        },
      });

      map.addLayer({
        id: "pinned-reds-dot",
        source: "pinned-reds",
        type: "circle",
        paint: {
          "circle-color": "#ff1a1a",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 18, 9],
          "circle-opacity": 1,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
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

      // ── Scout trail overlays (one per scout slot, built live as reports arrive) ──
      for (let i = 0; i < MAX_SCOUT_TRAILS; i++) {
        const color = TRAIL_COLORS[i];

        map.addSource(`scout-trail-${i}`, {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });

        // Dashed path line
        map.addLayer({
          id: `scout-trail-line-${i}`,
          source: `scout-trail-${i}`,
          type: "line",
          filter: ["==", "$type", "LineString"],
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": color,
            "line-width": 1.8,
            "line-opacity": 0.7,
            "line-dasharray": [2.5, 1.5],
          },
        });

        // Node glow
        map.addLayer({
          id: `scout-trail-node-glow-${i}`,
          source: `scout-trail-${i}`,
          type: "circle",
          filter: ["==", "$type", "Point"],
          paint: {
            "circle-color": color,
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 9, 18, 20],
            "circle-opacity": 0.15,
            "circle-blur": 0.75,
            "circle-stroke-width": 0,
          },
        });

        // Node dot — current position is larger and fully opaque
        map.addLayer({
          id: `scout-trail-node-dot-${i}`,
          source: `scout-trail-${i}`,
          type: "circle",
          filter: ["==", "$type", "Point"],
          paint: {
            "circle-color": color,
            "circle-radius": [
              "interpolate", ["linear"], ["zoom"],
              13, ["case", ["get", "isCurrent"], 4.5, 2.5],
              18, ["case", ["get", "isCurrent"], 9,   5],
            ],
            "circle-opacity": ["case", ["get", "isCurrent"], 1, 0.6],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": ["case", ["get", "isCurrent"], 1.5, 0.8],
          },
        });
      }

      // ── Tactical route overlay (fly mode) ───────────────────────────────────
      map.addSource("tactical-route", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "tactical-route-glow",
        source: "tactical-route",
        type: "line",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#00ff44",
          "line-width": 10,
          "line-opacity": 0.18,
          "line-blur": 4,
        },
      });

      map.addLayer({
        id: "tactical-route-line",
        source: "tactical-route",
        type: "line",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#00ff44",
          "line-width": 2.5,
          "line-opacity": 0.9,
        },
      });

      map.addSource("tactical-stops", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.addLayer({
        id: "tactical-stops-glow",
        source: "tactical-stops",
        type: "circle",
        paint: {
          "circle-color": "#00ff44",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 10, 18, 22],
          "circle-opacity": 0.2,
          "circle-blur": 0.7,
          "circle-stroke-width": 0,
        },
      });

      map.addLayer({
        id: "tactical-stops-dot",
        source: "tactical-stops",
        type: "circle",
        paint: {
          "circle-color": "#00ff44",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 4, 18, 9],
          "circle-opacity": 1,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
        },
      });

      map.addLayer({
        id: "tactical-stops-label",
        source: "tactical-stops",
        type: "symbol",
        layout: {
          "text-field": ["to-string", ["get", "index"]],
          "text-size": ["interpolate", ["linear"], ["zoom"], 13, 9, 18, 13],
          "text-anchor": "center",
          "text-font": ["DIN Pro Bold", "Arial Unicode MS Bold"],
        },
        paint: {
          "text-color": "#000000",
          "text-halo-color": "#00ff44",
          "text-halo-width": 0.5,
        },
      });

      const COLOR_LABEL: Record<string, string> = {
        RED: "CRITICAL",
        ORANGE: "HIGH PRIORITY",
        YELLOW: "MODERATE",
        GREEN: "LOW IMPACT",
      };
      const COLOR_HEX_LABEL: Record<string, string> = {
        RED: "#ff1a1a",
        ORANGE: "#fb923c",
        YELLOW: "#eab308",
        GREEN: "#22c55e",
      };

      map.on("mouseenter", "tactical-stops-dot", (e) => {
        map.getCanvas().style.cursor = "pointer";
        if (!e.features?.[0]) return;
        const props = e.features[0].properties as {
          index: number;
          name: string;
          color: string;
          reason: string;
          height_m: number;
          material: string;
          damage_probability: number;
        };
        hoverTimerRef.current = setTimeout(() => {
          hoverPopupRef.current?.remove();
          const dmgPct = Math.round((props.damage_probability ?? 0) * 100);
          const html = `
            <div style="font-family:monospace;font-size:11px;max-width:240px;line-height:1.6;color:#e2e8f0;background:rgba(8,10,18,0.95);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:12px 14px;backdrop-filter:blur(8px);">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                <span style="font-size:18px;font-weight:900;color:#00ff44;">.${props.index}.</span>
                <span style="font-size:12px;font-weight:bold;color:#f8fafc;">${props.name}</span>
              </div>
              <div style="display:inline-block;padding:2px 7px;border-radius:4px;background:${COLOR_HEX_LABEL[props.color] ?? "#94a3b8"}22;color:${COLOR_HEX_LABEL[props.color] ?? "#94a3b8"};font-weight:bold;font-size:10px;letter-spacing:0.05em;margin-bottom:8px;">${COLOR_LABEL[props.color] ?? props.color}</div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin-bottom:8px;color:#94a3b8;">
                <span>Height</span><span style="color:#e2e8f0;">${props.height_m ? props.height_m.toFixed(1) + " m" : "-"}</span>
                <span>Material</span><span style="color:#e2e8f0;">${props.material || "Unknown"}</span>
                <span>Damage risk</span><span style="color:#e2e8f0;">${dmgPct}%</span>
              </div>
              <div style="color:#cbd5e1;border-top:1px solid rgba(255,255,255,0.07);padding-top:8px;font-size:10.5px;">${props.reason}</div>
            </div>`;
          hoverPopupRef.current = new mapboxgl.Popup({
            closeButton: false,
            maxWidth: "280px",
            className: "aegis-stop-popup",
          })
            .setLngLat(
              (e.features![0].geometry as GeoJSON.Point).coordinates as [number, number],
            )
            .setHTML(html)
            .addTo(map);
        }, 600);
      });

      map.on("mouseleave", "tactical-stops-dot", () => {
        map.getCanvas().style.cursor = "";
        if (hoverTimerRef.current) {
          clearTimeout(hoverTimerRef.current);
          hoverTimerRef.current = null;
        }
        hoverPopupRef.current?.remove();
        hoverPopupRef.current = null;
      });

      map.doubleClickZoom.disable();
      map.on("dblclick", (e) => {
        if (flyModeRef.current) return;
        onMapDoubleClickRef.current?.(e.lngLat.lat, e.lngLat.lng);
      });

      // ── Click handler (triage buildings) ─────────────────────────────────────
      map.on("click", "triage-buildings-3d", (e) => {
        if (flyModeRef.current) return;
        if (!e.features?.[0]) return;
        const props = e.features[0].properties as { building_id: string };
        const building = buildingsRef.current.find(
          (b) => b.id === props.building_id,
        );
        if (building) onBuildingClick(building, { x: e.point.x, y: e.point.y });
      });
      map.on("click", "triage-markers-circle", (e) => {
        if (flyModeRef.current) return;
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

      setMapLoaded(true);
    });

    return () => {
      // intentionally not removing the map on effect re-runs (React strict mode)
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cancel the trail animation RAF on unmount.
  useEffect(() => {
    return () => {
      if (trailAnimRafRef.current !== null) {
        cancelAnimationFrame(trailAnimRafRef.current);
      }
    };
  }, []);

  // Fly mode transitions and camera control
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    if (!flyMode) {
      const s = flyStateRef.current;
      s.active = false;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (document.pointerLockElement === containerRef.current) {
        document.exitPointerLock();
      }
      map.dragPan.enable();
      map.scrollZoom.enable();
      map.dragRotate.enable();
      map.touchZoomRotate.enable();
      map.touchZoomRotate.enableRotation?.();
      if (lastOrbitRef.current) {
        const snap = lastOrbitRef.current;
        map.easeTo({
          center: snap.center,
          zoom: snap.zoom,
          pitch: snap.pitch,
          bearing: snap.bearing,
          duration: 800,
        });
      }
      return;
    }

    if (!flyTarget) return;
    const targetKey = `${flyTarget.lat.toFixed(6)}:${flyTarget.lng.toFixed(6)}`;
    if (flyStateRef.current.active && lastFlyTargetRef.current === targetKey) {
      return;
    }
    lastFlyTargetRef.current = targetKey;

    if (!flyStateRef.current.active) {
      const c = map.getCenter();
      lastOrbitRef.current = {
        center: [c.lng, c.lat],
        zoom: map.getZoom(),
        pitch: map.getPitch(),
        bearing: map.getBearing(),
      };
    }

    const s = flyStateRef.current;
    s.active = false;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    s.lng = flyTarget.lng;
    s.lat = flyTarget.lat;
    s.alt = FLY_START_ALT;
    s.bearing = FLY_START_BEARING;
    s.pitch = FLY_START_PITCH;
    s.velLng = 0;
    s.velLat = 0;
    s.velAlt = 0;
    s.keys.clear();

    map.dragPan.disable();
    map.scrollZoom.disable();
    map.dragRotate.disable();
    map.touchZoomRotate.disable();
    map.touchZoomRotate.disableRotation?.();

    const handleTransitionEnd = () => {
      const camera = map.getFreeCameraOptions();
      camera.position = mapboxgl.MercatorCoordinate.fromLngLat(
        { lng: s.lng, lat: s.lat },
        s.alt,
      );
      camera.setPitchBearing(s.pitch, s.bearing);
      map.setFreeCameraOptions(camera);
      s.active = true;
      s.lastTime = performance.now();
      startFlyLoop();
    };

    map.once("moveend", handleTransitionEnd);
    map.easeTo({
      center: [flyTarget.lng, flyTarget.lat],
      zoom: 16.5,
      pitch: 70,
      bearing: FLY_START_BEARING,
      duration: 1200,
      easing: (t) => t,
    });

    return () => {
      map.off("moveend", handleTransitionEnd);
    };
  }, [flyMode, flyTarget, mapLoaded, startFlyLoop]);

  useEffect(() => {
    if (!flyMode) return;

    const container = containerRef.current;
    if (!container) return;

    const onMouseDown = () => {
      if (document.pointerLockElement !== container) {
        container.requestPointerLock?.();
      }
    };
    const onMove = (e: MouseEvent) => {
      const s = flyStateRef.current;
      if (!s.active) return;

      let dx: number;
      let dy: number;
      if (document.pointerLockElement === containerRef.current) {
        dx = e.movementX;
        dy = e.movementY;
      } else {
        if (e.buttons === 0) return;
        dx = e.movementX;
        dy = e.movementY;
      }

      s.bearing += dx * FLY_SENSITIVITY * (180 / Math.PI);
      s.pitch = Math.max(
        FLY_PITCH_MIN,
        Math.min(FLY_PITCH_MAX, s.pitch + dy * FLY_SENSITIVITY * (180 / Math.PI)),
      );
    };

    container.addEventListener("mousedown", onMouseDown);
    document.addEventListener("mousemove", onMove);
    return () => {
      container.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mousemove", onMove);
    };
  }, [flyMode]);

  useEffect(() => {
    if (!flyMode) return;

    const onDown = (e: KeyboardEvent) => {
      flyStateRef.current.keys.add(e.key.toLowerCase());
      if (e.key === "Escape") {
        if (document.pointerLockElement === containerRef.current) {
          document.exitPointerLock();
        }
        flyStateRef.current.active = false;
        if (rafRef.current) {
          cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
        }
        onFlyExit?.();
      }
      if (
        [" ", "w", "a", "s", "d"].includes(e.key.toLowerCase()) &&
        flyStateRef.current.active
      ) {
        e.preventDefault();
      }
    };
    const onUp = (e: KeyboardEvent) =>
      flyStateRef.current.keys.delete(e.key.toLowerCase());

    document.addEventListener("keydown", onDown);
    document.addEventListener("keyup", onUp);
    return () => {
      document.removeEventListener("keydown", onDown);
      document.removeEventListener("keyup", onUp);
    };
  }, [flyMode, onFlyExit]);

  // Update tactical route layers whenever the route changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;
    const routeSrc = map.getSource("tactical-route") as
      | mapboxgl.GeoJSONSource
      | undefined;
    const stopSrc = map.getSource("tactical-stops") as
      | mapboxgl.GeoJSONSource
      | undefined;
    if (!routeSrc || !stopSrc) return;

    if (!flyRoute || flyRoute.length === 0) {
      routeSrc.setData({ type: "FeatureCollection", features: [] });
      stopSrc.setData({ type: "FeatureCollection", features: [] });
      return;
    }

    routeSrc.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "LineString",
            coordinates: flyRoute.map((wp) => [wp.lng, wp.lat]),
          },
          properties: {},
        },
      ],
    });

    const hazardStops = flyRoute.filter((wp) => wp.hazard);
    stopSrc.setData({
      type: "FeatureCollection",
      features: hazardStops.map((wp, i) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [wp.lng, wp.lat] },
        properties: {
          index: i + 1,
          name: wp.hazard!.label,
          color: hazardTypeToTriageColor(wp.hazard!.type),
          score: 4,
          height_m: 0,
          material: "Unknown",
          damage_probability: 0,
          reason: wp.hazard!.label,
        },
      })),
    });
  }, [flyRoute, mapLoaded]);

  // Detect newly-arrived trail points and start per-slot animations.
  // The actual GeoJSON updates happen inside runTrailAnim each RAF frame.
  useEffect(() => {
    if (!mapLoaded) return;

    for (let i = 0; i < MAX_SCOUT_TRAILS; i++) {
      const trail = scoutTrails?.[i];
      const committed = committedLastPtRef.current.get(i);

      if (!trail || trail.points.length === 0) {
        // Trail was cleared — reset slot state.
        committedLastPtRef.current.delete(i);
        slotAnimRef.current.delete(i);
        continue;
      }

      const newLast = trail.points[trail.points.length - 1];

      if (!committed) {
        // First point for this scout — commit immediately, no tween.
        committedLastPtRef.current.set(i, newLast);
      } else if (newLast.lat !== committed.lat || newLast.lng !== committed.lng) {
        // New terminal point arrived — start a 1.2-second tween.
        slotAnimRef.current.set(i, {
          fromPt: committed,
          toPt: newLast,
          startTime: performance.now(),
          duration: 1200,
        });
      }
    }

    // Kick off the animation loop (or restart it so static trails also redraw).
    if (trailAnimRafRef.current !== null) {
      cancelAnimationFrame(trailAnimRafRef.current);
    }
    trailAnimRafRef.current = requestAnimationFrame(runTrailAnim);
  }, [scoutTrails, mapLoaded, runTrailAnim]);

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

  // Pinned reds — only ever grows, never clears
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("pinned-reds") as mapboxgl.GeoJSONSource | undefined;
    if (!source) return;
    source.setData({
      type: "FeatureCollection",
      features: pinnedReds.map((b) => buildingToMarkerFeature(b)),
    });
  }, [pinnedReds]);

  // Fly to new center
  useEffect(() => {
    if (!center || !mapRef.current || flyMode) return;
    mapRef.current.flyTo({
      center,
      zoom: DEFAULT_ZOOM,
      pitch: 50,
      duration: 1800,
    });
  }, [center, flyMode]);

  // Toggle between color overlay (bird's eye) and dot markers (fly mode)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;
    if (flyMode) {
      // fly mode: hide overlay cap, show dots
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-base", 0);
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-height", ["get", "height_m"]);
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-opacity", 0.28);
      map.setPaintProperty("triage-markers-circle", "circle-opacity", 0.95);
      map.setPaintProperty("triage-markers-circle", "circle-radius", ["interpolate", ["linear"], ["zoom"], 13, 2.75, 18, 8]);
      map.setPaintProperty("triage-markers-glow", "circle-opacity", 0.2);
      map.setPaintProperty("triage-markers-glow", "circle-radius", ["interpolate", ["linear"], ["zoom"], 13, 6, 18, 18]);
    } else {
      // bird's eye: show overlay cap, hide dots
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-base", ["get", "height_m"]);
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-height", ["+", ["get", "height_m"], 25]);
      map.setPaintProperty("triage-buildings-3d", "fill-extrusion-opacity", 0.85);
      map.setPaintProperty("triage-markers-circle", "circle-opacity", 0);
      map.setPaintProperty("triage-markers-circle", "circle-radius", 8);
      map.setPaintProperty("triage-markers-glow", "circle-opacity", 0);
      map.setPaintProperty("triage-markers-glow", "circle-radius", 0);
    }
  }, [flyMode, mapLoaded]);

  // If an overlay needs space near a clicked building, zoom out slightly.
  useEffect(() => {
    if (!ensureSpaceForOverlay || !mapRef.current || flyMode) return;
    const map = mapRef.current;
    map.easeTo({
      zoom: Math.max(map.getZoom() - 0.8, 12),
      duration: 700,
    });
  }, [ensureSpaceForOverlay, flyMode]);

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
  // b.lat/lng can be 0 or missing if the backend didn't populate them.
  // Fall back to the footprint centroid so the dot always lands on the building.
  let lat = b.lat;
  let lng = b.lng;
  if ((!lat || !lng) && b.footprint?.length > 0) {
    let sumLat = 0, sumLng = 0;
    for (const [la, ln] of b.footprint) { sumLat += la; sumLng += ln; }
    lat = sumLat / b.footprint.length;
    lng = sumLng / b.footprint.length;
  }
  return {
    type: "Feature",
    id: `${b.id}-marker`,
    geometry: { type: "Point", coordinates: [lng, lat] },
    properties: {
      building_id: b.id,
      color_hex: TRIAGE_HEX[b.color],
    },
  };
}

function hazardTypeToTriageColor(type: string): string {
  switch (type) {
    case "blocked":
      return "RED";
    case "overhead":
      return "ORANGE";
    case "medical":
      return "RED";
    case "turn":
      return "YELLOW";
    case "arrival":
      return "GREEN";
    default:
      return "ORANGE";
  }
}
