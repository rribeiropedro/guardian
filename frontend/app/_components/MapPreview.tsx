"use client";

import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

const CENTER: [number, number] = [-73.9857, 40.7580];

export default function MapPreview() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
    if (!token) return;

    mapboxgl.accessToken = token;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: "mapbox://styles/mapbox/standard",
      center: CENTER,
      zoom: 15.3,
      pitch: 55,
      bearing: -20,
      antialias: true,
      interactive: false,
    });

    mapRef.current = map;

    map.on("load", () => {
      map.setConfigProperty("basemap", "lightPreset", "night");

      // Slow rotation
      let bearing = -20;
      const rotate = () => {
        bearing -= 0.04;
        map.setBearing(bearing);
        requestAnimationFrame(rotate);
      };
      rotate();
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  return <div ref={containerRef} className="w-full h-full" />;
}
