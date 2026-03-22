"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN!;

const START_LNG = -80.4234;
const START_LAT = 37.2284;
const START_ALT = 80;
const SPEED = 10;
const SPRINT_MULT = 2.5;
const SENSITIVITY = 0.003;
const PITCH_MIN = -60;
const PITCH_MAX = 80;
const VERT_SPEED = 8;
const DAMPING = 0.88;
const UNIFORM_LIGHT = { anchor: "viewport" as const, color: "white", intensity: 0.08 };

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
  locationName?: string;
}

export default function FlyView({
  initialLat,
  initialLng,
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
    bearing: 0,
    pitch: 20,
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
      style: "mapbox://styles/mapbox/navigation-night-v1",
      center: [startLng, startLat],
      zoom: 15.5,
      pitch: 50,
      antialias: true,
    });
    mapRef.current = map;

    map.on("load", () => {
      // Keep building tones uniform by minimizing directional lighting.
      map.setLight(UNIFORM_LIGHT);

      const layers = map.getStyle().layers;
      let firstSymbolId: string | undefined;
      for (const layer of layers) {
        if (layer.type === "symbol") {
          firstSymbolId = layer.id;
          break;
        }
      }
      map.addLayer(
        {
          id: "buildings-3d",
          source: "composite",
          "source-layer": "building",
          filter: ["==", "extrude", "true"],
          type: "fill-extrusion",
          minzoom: 13,
          paint: {
            "fill-extrusion-color": "#1e293b",
            "fill-extrusion-height": [
              "interpolate",
              ["linear"],
              ["zoom"],
              14,
              0,
              14.05,
              ["get", "height"],
            ],
            "fill-extrusion-base": [
              "interpolate",
              ["linear"],
              ["zoom"],
              14,
              0,
              14.05,
              ["get", "min_height"],
            ],
            "fill-extrusion-opacity": 0.95,
            "fill-extrusion-vertical-gradient": false,
          },
        },
        firstSymbolId,
      );
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [startLat, startLng]);

  useEffect(() => {
    const onChange = () => {
      const canvas = mapRef.current?.getCanvas();
      const locked = document.pointerLockElement === canvas;
      stateRef.current.active = locked;
      setActive(locked);
      if (locked) {
        mapRef.current?.dragPan.disable();
        mapRef.current?.scrollZoom.disable();
        mapRef.current?.dragRotate.disable();
        stateRef.current.lastTime = performance.now();
        stateRef.current.keys.clear();
        setHint(true);
        setTimeout(() => setHint(false), 3000);
        loop();
      } else {
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        mapRef.current?.dragPan.enable();
        mapRef.current?.scrollZoom.enable();
        mapRef.current?.dragRotate.enable();
      }
    };
    document.addEventListener("pointerlockchange", onChange);
    return () => document.removeEventListener("pointerlockchange", onChange);
  }, [loop]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const s = stateRef.current;
      if (!s.active) return;
      s.bearing += e.movementX * SENSITIVITY * (180 / Math.PI);
      s.pitch = Math.max(
        PITCH_MIN,
        Math.min(
          PITCH_MAX,
          s.pitch + e.movementY * SENSITIVITY * (180 / Math.PI),
        ),
      );
    };
    document.addEventListener("mousemove", onMove);
    return () => document.removeEventListener("mousemove", onMove);
  }, []);

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      stateRef.current.keys.add(e.key.toLowerCase());
      if (e.key === "Escape") document.exitPointerLock();
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
        {!active && (
          <button
            onClick={() => mapRef.current?.getCanvas().requestPointerLock()}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-mono font-bold tracking-wider transition-colors"
          >
            ENTER FIRST-PERSON →
          </button>
        )}
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
          WASD move · Mouse look · Space up · Shift down · Esc exit
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
