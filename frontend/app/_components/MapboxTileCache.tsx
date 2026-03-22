"use client";

import { useEffect } from "react";

const MAPBOX_SW_PATH = "/mapbox-cache-sw.js";

export default function MapboxTileCache() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker
      .register(MAPBOX_SW_PATH)
      .catch((error) => {
        // Avoid breaking app flow if SW registration fails.
        console.warn("Mapbox tile cache service worker registration failed:", error);
      });
  }, []);

  return null;
}
