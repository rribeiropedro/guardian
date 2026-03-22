// Tactical route planner for first-person view.
//
// Selection logic:
//   ≥5 RED buildings  → connect reds (cap 15)
//   otherwise         → K-means, pick highest-impact cluster (cap 15)
//
// Ordering: try every starting point for nearest-neighbour, keep shortest path.
// Stops: snap each hotspot centroid to its nearest point on the road geometry,
//        then deduplicate stops that are within ~40 m of each other.

export interface TriageBuilding {
  id: string;
  name?: string;
  color: "RED" | "ORANGE" | "YELLOW" | "GREEN";
  height_m: number;
  material?: string;
  triage_score?: number;
  damage_probability?: number;
  footprint: [number, number][]; // [lat, lng] pairs
}

export interface RouteStop {
  lat: number;
  lng: number;
  id: string;
  name: string;
  color: "RED" | "ORANGE" | "YELLOW" | "GREEN";
  score: number;
  height_m: number;
  material: string;
  damage_probability: number;
  reason: string;
}

export interface RouteResult {
  geometry: GeoJSON.LineString;
  stops: RouteStop[];
}

interface Pt {
  lat: number;
  lng: number;
  score: number;
  id: string;
  name: string;
  color: "RED" | "ORANGE" | "YELLOW" | "GREEN";
  height_m: number;
  material: string;
  damage_probability: number;
}

const COLOR_SCORE: Record<string, number> = { RED: 4, ORANGE: 3, YELLOW: 2, GREEN: 1 };

const STOP_REASON: Record<string, string> = {
  RED: "Critical structural failure risk. Immediate rescue operations required — high casualty probability.",
  ORANGE: "Significant damage detected. Triage and evacuation support needed. Assess structural integrity before entry.",
  YELLOW: "Moderate impact. Precautionary evacuation recommended. Monitor for structural deterioration.",
  GREEN: "Low direct impact. Welfare check required. May serve as staging area for adjacent critical zones.",
};

// ~40 m in degrees — stops closer than this share a single dot
const DEDUP_THRESHOLD = 0.00036;

// ── Geometry helpers ──────────────────────────────────────────────────────────

function centroid(footprint: [number, number][]): { lat: number; lng: number } {
  if (footprint.length === 0) return { lat: 0, lng: 0 };
  let lat = 0, lng = 0;
  for (const [la, ln] of footprint) { lat += la; lng += ln; }
  return { lat: lat / footprint.length, lng: lng / footprint.length };
}

function dist2d(aLat: number, aLng: number, bLat: number, bLng: number): number {
  return Math.sqrt((aLat - bLat) ** 2 + (aLng - bLng) ** 2);
}

function ptDist(a: Pt, b: Pt) { return dist2d(a.lat, a.lng, b.lat, b.lng); }

function snapToSegment(
  pLat: number, pLng: number,
  aLat: number, aLng: number,
  bLat: number, bLng: number,
): { lat: number; lng: number } {
  const dx = bLng - aLng, dy = bLat - aLat;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { lat: aLat, lng: aLng };
  const t = Math.max(0, Math.min(1, ((pLng - aLng) * dx + (pLat - aLat) * dy) / lenSq));
  return { lat: aLat + t * dy, lng: aLng + t * dx };
}

function snapToLine(
  pt: { lat: number; lng: number },
  line: GeoJSON.LineString,
): { lat: number; lng: number } {
  const coords = line.coordinates;
  let best = { lat: coords[0][1], lng: coords[0][0] };
  let bestD = Infinity;
  for (let i = 0; i < coords.length - 1; i++) {
    const snap = snapToSegment(
      pt.lat, pt.lng,
      coords[i][1], coords[i][0],
      coords[i + 1][1], coords[i + 1][0],
    );
    const d = dist2d(pt.lat, pt.lng, snap.lat, snap.lng);
    if (d < bestD) { bestD = d; best = snap; }
  }
  return best;
}

// ── K-means ───────────────────────────────────────────────────────────────────

function kMeans(pts: Pt[], k: number, iterations = 30): Pt[][] {
  if (pts.length <= k) return pts.map((p) => [p]);
  const step = Math.max(1, Math.floor(pts.length / k));
  // Centroids are spatial only — we just need lat/lng for distance
  let centroids = Array.from({ length: k }, (_, i) => ({
    lat: pts[i * step].lat,
    lng: pts[i * step].lng,
  }));
  const assignments = new Array<number>(pts.length).fill(0);

  for (let iter = 0; iter < iterations; iter++) {
    for (let i = 0; i < pts.length; i++) {
      let best = 0, bestD = Infinity;
      for (let j = 0; j < k; j++) {
        const d = dist2d(pts[i].lat, pts[i].lng, centroids[j].lat, centroids[j].lng);
        if (d < bestD) { bestD = d; best = j; }
      }
      assignments[i] = best;
    }
    const sums = Array.from({ length: k }, () => ({ lat: 0, lng: 0, count: 0 }));
    for (let i = 0; i < pts.length; i++) {
      sums[assignments[i]].lat += pts[i].lat;
      sums[assignments[i]].lng += pts[i].lng;
      sums[assignments[i]].count++;
    }
    for (let j = 0; j < k; j++) {
      if (sums[j].count > 0) {
        centroids[j] = { lat: sums[j].lat / sums[j].count, lng: sums[j].lng / sums[j].count };
      }
    }
  }

  const clusters: Pt[][] = Array.from({ length: k }, () => []);
  for (let i = 0; i < pts.length; i++) clusters[assignments[i]].push(pts[i]);
  return clusters.filter((c) => c.length > 0);
}

// ── Nearest-neighbour — try every start, keep shortest total path ─────────────

function nnFrom(startIdx: number, pts: Pt[]): Pt[] {
  const remaining = [...pts];
  const route: Pt[] = [remaining.splice(startIdx, 1)[0]];
  while (remaining.length > 0) {
    const last = route[route.length - 1];
    let bestIdx = 0, bestD = Infinity;
    for (let i = 0; i < remaining.length; i++) {
      const d = ptDist(last, remaining[i]);
      if (d < bestD) { bestD = d; bestIdx = i; }
    }
    route.push(remaining.splice(bestIdx, 1)[0]);
  }
  return route;
}

function totalDist(pts: Pt[]): number {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += ptDist(pts[i - 1], pts[i]);
  return d;
}

function bestNearestNeighbor(pts: Pt[]): Pt[] {
  if (pts.length <= 2) return [...pts];
  let best: Pt[] = [], bestD = Infinity;
  for (let i = 0; i < pts.length; i++) {
    const candidate = nnFrom(i, pts);
    const d = totalDist(candidate);
    if (d < bestD) { bestD = d; best = candidate; }
  }
  return best;
}

// ── Stop deduplication ────────────────────────────────────────────────────────

function deduplicateStops(stops: RouteStop[]): RouteStop[] {
  const result: RouteStop[] = [];
  for (const s of stops) {
    const tooClose = result.some(
      (r) => dist2d(r.lat, r.lng, s.lat, s.lng) < DEDUP_THRESHOLD,
    );
    if (!tooClose) result.push(s);
  }
  return result;
}

// ── Mapbox Directions API ─────────────────────────────────────────────────────

async function fetchRoute(
  waypoints: { lat: number; lng: number }[],
  token: string,
): Promise<GeoJSON.LineString | null> {
  const capped = waypoints.slice(0, 25);
  if (capped.length < 2) return null;
  const coords = capped.map((p) => `${p.lng},${p.lat}`).join(";");
  const url =
    `https://api.mapbox.com/directions/v5/mapbox/driving/${coords}` +
    `?geometries=geojson&overview=full&access_token=${token}`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      routes?: { geometry?: GeoJSON.LineString }[];
    };
    return data.routes?.[0]?.geometry ?? null;
  } catch {
    return null;
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function planTacticalRoute(
  buildings: TriageBuilding[],
  token: string,
): Promise<RouteResult | null> {
  if (buildings.length < 2) return null;

  const pts: Pt[] = buildings.map((b) => ({
    ...centroid(b.footprint),
    score: COLOR_SCORE[b.color] ?? 1,
    id: b.id,
    name: b.name ?? b.id,
    color: b.color,
    height_m: b.height_m,
    material: b.material ?? "Unknown",
    damage_probability: b.damage_probability ?? 0,
  }));

  const reds = pts.filter((p) => p.color === "RED");

  let targets: Pt[];
  let selectionMode: "reds" | "cluster";

  if (reds.length >= 5) {
    targets = reds;
    selectionMode = "reds";
  } else {
    const k = Math.max(2, Math.min(5, Math.floor(pts.length / 3)));
    const clusters = kMeans(pts, k);
    clusters.sort(
      (a, b) =>
        b.reduce((s, p) => s + p.score, 0) / b.length -
        a.reduce((s, p) => s + p.score, 0) / a.length,
    );
    targets = clusters[0];
    selectionMode = "cluster";
  }

  if (targets.length > 15) targets = targets.slice(0, 15);
  if (targets.length < 2) return null;

  const ordered = bestNearestNeighbor(targets);
  const geometry = await fetchRoute(ordered, token);
  if (!geometry) return null;

  const rawStops: RouteStop[] = ordered.map((pt, i) => {
    const snapped = snapToLine(pt, geometry);
    const baseReason = STOP_REASON[pt.color] ?? STOP_REASON.GREEN;
    const selectionReason =
      selectionMode === "reds"
        ? "Identified as part of the critical red-zone cluster."
        : `Highest-impact K-means cluster (stop ${i + 1}).`;
    return {
      lat: snapped.lat,
      lng: snapped.lng,
      id: pt.id,
      name: pt.name,
      color: pt.color,
      score: pt.score,
      height_m: pt.height_m,
      material: pt.material,
      damage_probability: pt.damage_probability,
      reason: `${selectionReason} ${baseReason}`,
    };
  });

  const stops = deduplicateStops(rawStops);
  return { geometry, stops };
}
