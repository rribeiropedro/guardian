"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { planTacticalRoute } from "../_lib/routePlanner";

const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN!;
const TRIAGE_HEX = {
  RED: "#ff1a1a",
  ORANGE: "#fb923c",
  YELLOW: "#a37c00",
  GREEN: "#22c55e",
} as const;
const STANDARD_STYLE = "mapbox://styles/mapbox/standard";

const START_LNG = -80.4234;
const START_LAT = 37.2284;
const START_ALT = 90;
const START_BEARING = 95;
const START_PITCH = 100;
const SPEED = 14;
const SPRINT_MULT = 2.5;
const SENSITIVITY = 0.003;
const PITCH_MIN = -85;
const PITCH_MAX = 85;
const VERT_SPEED = 8;
const DAMPING = 0.88;

interface State {
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

interface FlyViewProps {
  initialLat?: number;
  initialLng?: number;
  selectedBuildingId?: string;
  locationName?: string;
}

interface StoredTriageBuilding {
  id: string;
  name?: string;
  color: keyof typeof TRIAGE_HEX;
  height_m: number;
  material?: string;
  triage_score?: number;
  damage_probability?: number;
  footprint: [number, number][];
}

export default function FlyView({
  initialLat,
  initialLng,
  selectedBuildingId,
  locationName,
}: FlyViewProps) {
  const startLat = initialLat ?? START_LAT;
  const startLng = initialLng ?? START_LNG;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const rafRef = useRef<number | undefined>(undefined);
  const stateRef = useRef<State>({
    lng: startLng,
    lat: startLat,
    alt: START_ALT,
    bearing: START_BEARING,
    pitch: START_PITCH,
    velLng: 0,
    velLat: 0,
    velAlt: 0,
    keys: new Set(),
    active: false,
    lastTime: 0,
  });
  const [active, setActive] = useState(false);
  const [hint, setHint] = useState(false);

  const loop = useCallback(() => {
    const step = (now: number) => {
      const s = stateRef.current;
      if (!s.active) return;
      const dt = Math.min((now - s.lastTime) / 1000, 0.05);
      s.lastTime = now;

      const sprint = s.keys.has("shift");
      const speed = SPEED * (sprint ? SPRINT_MULT : 1);
      const bearRad = (s.bearing * Math.PI) / 180;

      let moveX = 0,
        moveY = 0;
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
        moveY -= Math.sin(bearRad);
      }

      const mag = Math.sqrt(moveX * moveX + moveY * moveY);
      if (mag > 0) {
        moveX /= mag;
        moveY /= mag;
      }

      const mPerDegLat = 111111;
      const mPerDegLng = 111111 * Math.cos((s.lat * Math.PI) / 180);
      s.velLng = s.velLng * DAMPING + (moveX * speed * dt) / mPerDegLng;
      s.velLat = s.velLat * DAMPING + (moveY * speed * dt) / mPerDegLat;
      s.lng += s.velLng;
      s.lat += s.velLat;

      let vert = 0;
      if (s.keys.has(" ") || s.keys.has("e")) vert = 1;
      if (s.keys.has("shift") || s.keys.has("q")) vert = -1;
      s.velAlt = s.velAlt * DAMPING + vert * VERT_SPEED * dt;
      s.alt = Math.max(2, s.alt + s.velAlt);

      const map = mapRef.current;
      if (!map) return;
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
    if (!containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = TOKEN;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: STANDARD_STYLE,
      center: [startLng, startLat],
      zoom: 15.5,
      pitch: 0,
      bearing: START_BEARING,
      antialias: true,
    });
    mapRef.current = map;

    map.on("load", () => {
      // Standard style with regular day/light basemap.
      if ("setConfigProperty" in map) {
        (
          map as mapboxgl.Map & {
            setConfigProperty?: (
              importId: string,
              configName: string,
              value: unknown,
            ) => void;
          }
        ).setConfigProperty?.("basemap", "lightPreset", "day");
      }

      const layers = map.getStyle().layers;
      let firstSymbolId: string | undefined;
      for (const layer of layers) {
        if (layer.type === "symbol") {
          firstSymbolId = layer.id;
          break;
        }
      }

      // Reuse triage colors from command center so first-person matches sky view.
      let triageBuildings: StoredTriageBuilding[] = [];
      try {
        const raw = sessionStorage.getItem("aegis_triage_buildings");
        if (raw) {
          const parsed: unknown = JSON.parse(raw);
          if (Array.isArray(parsed)) {
            triageBuildings = parsed.filter(isStoredTriageBuilding);
          }
        }
      } catch {
        // If storage is unavailable or malformed, continue without triage overlay.
      }

      // Only mark big buildings (12 m+ ≈ 3+ stories) as dots — no color wash.
      const bigBuildings = triageBuildings.filter((b) => b.height_m >= 12);

      if (bigBuildings.length > 0) {
        map.addSource("triage-markers", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: bigBuildings.map(toMarkerFeature),
          },
        });

        // Outer glow
        map.addLayer({
          id: "triage-markers-glow",
          source: "triage-markers",
          type: "circle",
          paint: {
            "circle-color": ["get", "color_hex"],
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              13,
              5,
              18,
              14,
            ],
            "circle-opacity": 0.12,
            "circle-blur": 0.8,
            "circle-stroke-width": 0,
          },
        });

        // Solid dot
        map.addLayer({
          id: "triage-markers-circle",
          source: "triage-markers",
          type: "circle",
          paint: {
            "circle-color": ["get", "color_hex"],
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              13,
              2,
              18,
              5,
            ],
            "circle-opacity": 0.65,
            "circle-stroke-color": "#f8fafc",
            "circle-stroke-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              13,
              0.8,
              18,
              1.2,
            ],
          },
        });

        // Selected building gets a blue ring marker
        if (selectedBuildingId) {
          map.addLayer({
            id: "selected-building-ring",
            source: "triage-markers",
            type: "circle",
            filter: ["==", ["get", "building_id"], selectedBuildingId],
            paint: {
              "circle-color": "transparent",
              "circle-radius": [
                "interpolate",
                ["linear"],
                ["zoom"],
                13,
                8,
                18,
                16,
              ],
              "circle-opacity": 0,
              "circle-stroke-color": "#60a5fa",
              "circle-stroke-width": 2.5,
            },
          });
        }
      }

      // ── Tactical route overlay (bright green road path) ──────────────────────
      map.addSource("tactical-route", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      // Glow halo under the line
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

      // Solid bright green line
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

      // ── Tactical stop dots ────────────────────────────────────────────────────
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

      // Number labels on each stop
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

      // Hover-delay tooltip on stops (shows after 600 ms of hovering)
      const COLOR_LABEL: Record<string, string> = {
        RED: "CRITICAL", ORANGE: "HIGH PRIORITY", YELLOW: "MODERATE", GREEN: "LOW IMPACT",
      };
      const COLOR_HEX_LABEL: Record<string, string> = {
        RED: "#ff1a1a", ORANGE: "#fb923c", YELLOW: "#eab308", GREEN: "#22c55e",
      };

      map.on("mouseenter", "tactical-stops-dot", (e) => {
        map.getCanvas().style.cursor = "pointer";
        if (!e.features?.[0] || !mapRef.current) return;
        const props = e.features[0].properties as {
          index: number; name: string; color: string; reason: string;
          height_m: number; material: string; damage_probability: number;
        };
        hoverTimerRef.current = setTimeout(() => {
          if (!mapRef.current) return;
          hoverPopupRef.current?.remove();
          const dmgPct = Math.round((props.damage_probability ?? 0) * 100);
          const html = `
            <div style="font-family:monospace;font-size:11px;max-width:240px;line-height:1.6;color:#e2e8f0;background:rgba(8,10,18,0.95);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:12px 14px;backdrop-filter:blur(8px);">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                <span style="font-size:18px;font-weight:900;color:#00ff44;">·${props.index}·</span>
                <span style="font-size:12px;font-weight:bold;color:#f8fafc;">${props.name}</span>
              </div>
              <div style="display:inline-block;padding:2px 7px;border-radius:4px;background:${COLOR_HEX_LABEL[props.color] ?? "#94a3b8"}22;color:${COLOR_HEX_LABEL[props.color] ?? "#94a3b8"};font-weight:bold;font-size:10px;letter-spacing:0.05em;margin-bottom:8px;">${COLOR_LABEL[props.color] ?? props.color}</div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin-bottom:8px;color:#94a3b8;">
                <span>Height</span><span style="color:#e2e8f0;">${props.height_m ? props.height_m.toFixed(1) + " m" : "—"}</span>
                <span>Material</span><span style="color:#e2e8f0;">${props.material || "Unknown"}</span>
                <span>Damage risk</span><span style="color:#e2e8f0;">${dmgPct}%</span>
              </div>
              <div style="color:#cbd5e1;border-top:1px solid rgba(255,255,255,0.07);padding-top:8px;font-size:10.5px;">${props.reason}</div>
            </div>`;
          hoverPopupRef.current = new mapboxgl.Popup({
            closeButton: false, maxWidth: "280px", className: "aegis-stop-popup",
          })
            .setLngLat((e.features![0].geometry as GeoJSON.Point).coordinates as [number, number])
            .setHTML(html)
            .addTo(mapRef.current);
        }, 600);
      });

      map.on("mouseleave", "tactical-stops-dot", () => {
        map.getCanvas().style.cursor = "";
        if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
        hoverPopupRef.current?.remove();
        hoverPopupRef.current = null;
      });

      // Kick off async route calculation
      if (triageBuildings.length >= 2) {
        planTacticalRoute(triageBuildings, TOKEN).then((result) => {
          if (!result || !mapRef.current) return;
          const routeSrc = mapRef.current.getSource("tactical-route") as mapboxgl.GeoJSONSource | undefined;
          routeSrc?.setData({
            type: "FeatureCollection",
            features: [{ type: "Feature", geometry: result.geometry, properties: {} }],
          });
          const stopSrc = mapRef.current.getSource("tactical-stops") as mapboxgl.GeoJSONSource | undefined;
          stopSrc?.setData({
            type: "FeatureCollection",
            features: result.stops.map((s, i) => ({
              type: "Feature" as const,
              geometry: { type: "Point" as const, coordinates: [s.lng, s.lat] },
              properties: {
                index: i + 1,
                name: s.name,
                color: s.color,
                score: s.score,
                height_m: s.height_m,
                material: s.material,
                damage_probability: s.damage_probability,
                reason: s.reason,
              },
            })),
          });
        });
      }

      // Force a forward-looking initial pose (not at the ground) before controls activate.
      const initialState = stateRef.current;
      const initialCamera = map.getFreeCameraOptions();
      initialCamera.position = mapboxgl.MercatorCoordinate.fromLngLat(
        { lng: initialState.lng, lat: initialState.lat },
        initialState.alt,
      );
      initialCamera.setPitchBearing(initialState.pitch, initialState.bearing);
      map.setFreeCameraOptions(initialCamera);

      // Auto-enter first-person mode when this view opens.
      stateRef.current.active = true;
      stateRef.current.lastTime = performance.now();
      stateRef.current.keys.clear();
      setActive(true);
      setHint(true);
      setTimeout(() => setHint(false), 3000);
      map.dragPan.disable();
      map.scrollZoom.disable();
      map.dragRotate.disable();
      loop();
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [loop, selectedBuildingId, startLat, startLng]);

  const mouseDownRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverPopupRef = useRef<mapboxgl.Popup | null>(null);

  useEffect(() => {
    const onMouseDown = (e: MouseEvent) => {
      mouseDownRef.current = true;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
      // Also try to grab pointer lock on click for smoother look
      const container = containerRef.current;
      if (container && document.pointerLockElement !== container) {
        container.requestPointerLock?.();
      }
    };
    const onMouseUp = () => {
      mouseDownRef.current = false;
    };
    const onMove = (e: MouseEvent) => {
      const s = stateRef.current;
      if (!s.active) return;

      let dx: number;
      let dy: number;
      if (document.pointerLockElement === containerRef.current) {
        // Pointer locked: use raw movement (no button required)
        dx = e.movementX;
        dy = e.movementY;
      } else {
        // Free drag: require mouse button held
        if (!mouseDownRef.current) return;
        dx = e.clientX - lastMouseRef.current.x;
        dy = e.clientY - lastMouseRef.current.y;
        lastMouseRef.current = { x: e.clientX, y: e.clientY };
      }

      s.bearing += dx * SENSITIVITY * (180 / Math.PI);
      s.pitch = Math.max(
        PITCH_MIN,
        Math.min(PITCH_MAX, s.pitch + dy * SENSITIVITY * (180 / Math.PI)),
      );
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("mousemove", onMove);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("mousemove", onMove);
    };
  }, []);

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      stateRef.current.keys.add(e.key.toLowerCase());
      if (e.key === "Escape") {
        if (document.pointerLockElement === containerRef.current) {
          document.exitPointerLock();
        }
        stateRef.current.active = false;
        setActive(false);
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
      }
      if (
        [" ", "w", "a", "s", "d"].includes(e.key.toLowerCase()) &&
        stateRef.current.active
      )
        e.preventDefault();
    };
    const onUp = (e: KeyboardEvent) =>
      stateRef.current.keys.delete(e.key.toLowerCase());
    document.addEventListener("keydown", onDown);
    document.addEventListener("keyup", onUp);
    return () => {
      document.removeEventListener("keydown", onDown);
      document.removeEventListener("keyup", onUp);
    };
  }, []);

  return (
    <div className="fixed inset-0 bg-[#0a0a0f]">
      <style>{`.aegis-stop-popup .mapboxgl-popup-content{background:transparent;padding:0;box-shadow:none}.aegis-stop-popup .mapboxgl-popup-tip{display:none}`}</style>
      <div
        ref={containerRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
        }}
      />

      <div className="absolute top-4 left-4 z-10 flex flex-col gap-2">
        <Link
          href="/"
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-slate-400 hover:text-white transition-colors"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
          >
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          COMMAND CENTER
        </Link>
        <div className="px-3 py-1.5 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md text-xs font-mono text-slate-400">
          {locationName || "Selected location"} · {startLat.toFixed(5)},{" "}
          {startLng.toFixed(5)}
        </div>
      </div>

      {active && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="w-5 h-5 relative opacity-60">
            <div className="absolute top-0 bottom-0 left-1/2 w-px bg-white" />
            <div className="absolute left-0 right-0 top-1/2 h-px bg-white" />
          </div>
        </div>
      )}

      {hint && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 px-4 py-2 rounded-lg border border-white/10 bg-[rgba(8,10,18,0.9)] backdrop-blur-md text-xs font-mono text-slate-400 text-center">
          Drag to look · WASD move · Space up · Shift down · Click to lock mouse
          · Esc exit
        </div>
      )}
      {active && !hint && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 text-[10px] font-mono text-slate-700 pointer-events-none">
          ESC to exit
        </div>
      )}
    </div>
  );
}

function toMarkerFeature(
  building: StoredTriageBuilding,
): GeoJSON.Feature<GeoJSON.Point> {
  // Compute centroid from footprint ([lat, lng] pairs)
  const fp = building.footprint;
  let sumLat = 0,
    sumLng = 0;
  for (const [lat, lng] of fp) {
    sumLat += lat;
    sumLng += lng;
  }
  const centroidLng = fp.length > 0 ? sumLng / fp.length : 0;
  const centroidLat = fp.length > 0 ? sumLat / fp.length : 0;

  return {
    type: "Feature",
    id: building.id,
    geometry: { type: "Point", coordinates: [centroidLng, centroidLat] },
    properties: {
      building_id: building.id,
      color_hex: TRIAGE_HEX[building.color],
    },
  };
}

function isStoredTriageBuilding(value: unknown): value is StoredTriageBuilding {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<StoredTriageBuilding>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.height_m === "number" &&
    Array.isArray(candidate.footprint) &&
    (candidate.color === "RED" ||
      candidate.color === "ORANGE" ||
      candidate.color === "YELLOW" ||
      candidate.color === "GREEN")
  );
}
