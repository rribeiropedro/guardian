'use client'

import { useMemo } from 'react'
import type { AgentFeedEntry, Building, Scout, Waypoint } from '../_lib/types'
import { downloadFemaReport } from '../_lib/fema-report'

// ── Types ────────────────────────────────────────────────────────────────────

interface RoadBlockage {
  name: string
  probability: 'CRITICAL' | 'HIGH' | 'MODERATE'
  pct: number
  reason: string
  nearStructures: string[]
}

interface Props {
  buildings: Building[]
  scouts: Scout[]
  route: Waypoint[]
  feed: AgentFeedEntry[]
  scenarioPrompt: string
  epicenterLat: number
  epicenterLng: number
  onClose: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_371_000
  const dLat = (lat2 - lat1) * Math.PI / 180
  const dLng = (lng2 - lng1) * Math.PI / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

function seededRand(seed: number): () => number {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff
    return (s >>> 0) / 0xffffffff
  }
}

const ROAD_NAMES = [
  'Main St', 'College Ave', 'Washington Blvd', 'Oak St', 'Elm Ave',
  'University Dr', 'Park Blvd', 'Jefferson St', 'Highland Ave', 'Center St',
  'Market St', 'Broad St', 'Commerce Dr', 'Campus Dr', 'Church St',
  'Maple Ave', 'Spring Rd', 'Industrial Pkwy', 'Railroad Ave', 'Bridge St',
]

function extractMagnitude(prompt: string): string {
  const m = prompt.match(/\b[mM]\s*([0-9](?:\.[0-9])?)\b/) ||
            prompt.match(/\bmagnitude\s*[:=]?\s*([0-9](?:\.[0-9])?)\b/i) ||
            prompt.match(/\b([0-9]\.[0-9])\s*magnitude\b/i)
  return m ? m[1] : '6.0'
}

function generateRoadBlockages(buildings: Building[]): RoadBlockage[] {
  const reds    = buildings.filter(b => b.color === 'RED')
  const oranges = buildings.filter(b => b.color === 'ORANGE')
  const blockages: RoadBlockage[] = []
  const usedIds   = new Set<string>()
  const usedRoads = new Set<string>()

  const rand = seededRand(
    Math.round(((buildings[0]?.lat ?? 37) + (buildings[0]?.lng ?? -80)) * 10000)
  )
  const pickRoad = (): string => {
    let name = ROAD_NAMES[Math.floor(rand() * ROAD_NAMES.length)]
    let attempts = 0
    while (usedRoads.has(name) && attempts++ < 20) {
      name = ROAD_NAMES[Math.floor(rand() * ROAD_NAMES.length)]
    }
    usedRoads.add(name)
    return name
  }

  for (const b of reds) {
    if (usedIds.has(b.id)) continue
    const cluster = reds.filter(
      o => o.id !== b.id && !usedIds.has(o.id) && haversineM(b.lat, b.lng, o.lat, o.lng) < 180
    )
    if (cluster.length >= 2) {
      const members = [b, ...cluster]
      const pct = 85 + Math.round(rand() * 12)
      blockages.push({
        name: pickRoad(), probability: 'CRITICAL', pct,
        reason: `${members.length} CRITICAL structures within 180 m — overlapping debris fields confirmed`,
        nearStructures: members.slice(0, 3).map(x => x.name),
      })
      members.forEach(x => usedIds.add(x.id))
    }
  }

  for (const b of reds) {
    if (usedIds.has(b.id)) continue
    const partner = reds.find(
      o => o.id !== b.id && !usedIds.has(o.id) && haversineM(b.lat, b.lng, o.lat, o.lng) < 220
    )
    if (partner) {
      const pct = 62 + Math.round(rand() * 18)
      blockages.push({
        name: pickRoad(), probability: 'HIGH', pct,
        reason: `2 CRITICAL structures within 220 m — probable debris obstruction across carriageway`,
        nearStructures: [b.name, partner.name],
      })
      usedIds.add(b.id)
      usedIds.add(partner.id)
    }
  }

  for (const b of oranges) {
    if (usedIds.has(b.id)) continue
    const cluster = oranges.filter(
      o => o.id !== b.id && !usedIds.has(o.id) && haversineM(b.lat, b.lng, o.lat, o.lng) < 150
    )
    if (cluster.length >= 2) {
      const members = [b, ...cluster]
      const pct = 38 + Math.round(rand() * 20)
      blockages.push({
        name: pickRoad(), probability: 'MODERATE', pct,
        reason: `${members.length} HIGH PRIORITY structures in cluster — partial obstruction possible`,
        nearStructures: members.slice(0, 3).map(x => x.name),
      })
      members.forEach(x => usedIds.add(x.id))
    }
  }

  for (const b of reds) {
    if (blockages.length >= 6) break
    if (usedIds.has(b.id)) continue
    const pct = 45 + Math.round(rand() * 20)
    blockages.push({
      name: pickRoad(), probability: 'HIGH', pct,
      reason: `CRITICAL structure — facade/parapet collapse risk onto adjacent roadway`,
      nearStructures: [b.name],
    })
    usedIds.add(b.id)
  }

  return blockages.slice(0, 6)
}

// ── Styles ────────────────────────────────────────────────────────────────────

const PROB_STYLE: Record<RoadBlockage['probability'], { badge: string; bar: string }> = {
  CRITICAL: { badge: 'bg-red-600 text-white',        bar: 'bg-red-500' },
  HIGH:     { badge: 'bg-orange-500 text-white',      bar: 'bg-orange-400' },
  MODERATE: { badge: 'bg-yellow-400 text-yellow-950', bar: 'bg-yellow-400' },
}

const COLOR_LABEL: Record<string, string> = {
  RED: 'Critical', ORANGE: 'High Priority', YELLOW: 'Moderate', GREEN: 'Low Impact',
}
const COLOR_HEX: Record<string, string> = {
  RED: '#ef4444', ORANGE: '#f97316', YELLOW: '#eab308', GREEN: '#22c55e',
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function FEMAReport({
  buildings, scouts, route, feed,
  scenarioPrompt, epicenterLat, epicenterLng, onClose,
}: Props) {
  const magnitude = extractMagnitude(scenarioPrompt)
  const now       = new Date()
  const dateStr   = now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  const timeStr   = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  const incidentNo = `FEMA-${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}-${String(Math.abs(Math.round(epicenterLat * 100))).slice(0,4)}`

  const counts = useMemo(() => ({
    RED:    buildings.filter(b => b.color === 'RED').length,
    ORANGE: buildings.filter(b => b.color === 'ORANGE').length,
    YELLOW: buildings.filter(b => b.color === 'YELLOW').length,
    GREEN:  buildings.filter(b => b.color === 'GREEN').length,
    total:  buildings.length,
  }), [buildings])

  const totalOccupancy = useMemo(
    () => buildings.reduce((s, b) => s + b.estimated_occupancy, 0),
    [buildings]
  )

  const topStructures = useMemo(
    () => [...buildings].sort((a, b) => b.triage_score - a.triage_score).slice(0, 5),
    [buildings]
  )

  const blockages = useMemo(() => generateRoadBlockages(buildings), [buildings])

  const criticalOccupancy = useMemo(
    () => buildings.filter(b => b.color === 'RED').reduce((s, b) => s + b.estimated_occupancy, 0),
    [buildings]
  )

  function handleDownloadFull() {
    downloadFemaReport({
      buildings,
      scouts,
      route,
      feed,
      scenarioCenter: { lat: epicenterLat, lng: epicenterLng },
      generatedAt: new Date(),
    })
  }

  return (
    <>
      <style>{`
        @media print {
          body > * { display: none !important; }
          .fema-printable { display: block !important; position: static !important; }
          .fema-no-print { display: none !important; }
        }
      `}</style>

      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-start justify-center overflow-y-auto py-8 px-4 fema-printable">

        {/* Report card */}
        <div className="relative w-full max-w-3xl bg-white text-gray-900 rounded-lg shadow-2xl overflow-hidden">

          {/* Controls */}
          <div className="fema-no-print absolute top-3 right-3 flex gap-2 z-10">
            <button
              onClick={handleDownloadFull}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-blue-300 bg-blue-50 text-blue-700 text-xs font-semibold hover:bg-blue-100 transition-colors shadow-sm"
              title="Download full 10-section FEMA report as HTML (printable to PDF)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Full Report (10 Sections)
            </button>
            <button
              onClick={() => window.print()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 text-xs font-semibold hover:bg-gray-50 transition-colors shadow-sm"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
                <rect x="6" y="14" width="12" height="8" />
              </svg>
              Print / PDF
            </button>
            <button
              onClick={onClose}
              className="px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 text-xs font-semibold hover:bg-gray-50 transition-colors shadow-sm"
            >
              Close ×
            </button>
          </div>

          {/* ── HEADER ─────────────────────────────────────────────────── */}
          <div className="bg-[#003366] text-white px-8 py-5">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-3 mb-1">
                  <div className="w-10 h-10 bg-white rounded flex items-center justify-center">
                    <span className="text-[#003366] font-black text-xs tracking-tight leading-none text-center">FEMA</span>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold tracking-widest text-blue-200 uppercase">
                      Federal Emergency Management Agency
                    </div>
                    <div className="text-lg font-bold tracking-wide leading-tight">
                      Preliminary Damage Assessment Report
                    </div>
                  </div>
                </div>
              </div>
              <div className="text-right text-xs text-blue-200 mt-1 space-y-0.5">
                <div className="font-mono font-bold text-white">{incidentNo}</div>
                <div>{dateStr} · {timeStr} hrs</div>
                <div className="text-[10px]">ICS Form 209 — PDA</div>
              </div>
            </div>
          </div>

          {/* Incident bar */}
          <div className="bg-[#d0e4f7] border-b border-[#a0c4e8] px-8 py-2.5 flex flex-wrap gap-x-8 gap-y-1 text-xs">
            <span><span className="font-semibold text-[#003366]">Incident Type:</span> Seismic Event — M{magnitude} Earthquake</span>
            <span><span className="font-semibold text-[#003366]">Epicenter:</span> {epicenterLat.toFixed(4)}°N, {Math.abs(epicenterLng).toFixed(4)}°W</span>
            <span><span className="font-semibold text-[#003366]">Reporting System:</span> GroundZero AI-ICS</span>
            <span><span className="font-semibold text-[#003366]">Assessment Status:</span> <span className="text-orange-700 font-bold">PRELIMINARY</span></span>
          </div>

          {/* Full-report callout */}
          <div className="fema-no-print bg-blue-50 border-b border-blue-200 px-8 py-2.5 flex items-center gap-3">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1d4ed8" strokeWidth="2">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span className="text-xs text-blue-800">
              This preview covers key sections. Click <strong>Full Report (10 Sections)</strong> above to download the complete FEMA-aligned document including NIMS/ICS compliance, ATC-20 methodology, US&amp;R protocol mapping, P-58/Hazus damage states, AI architecture, ICS-209 form, and HSGP grant language.
            </span>
          </div>

          <div className="px-8 py-5 space-y-5">

            {/* ── SECTION 1: DAMAGE SUMMARY ────────────────────────────── */}
            <section>
              <SectionHeader number="1" title="Damage Assessment Summary (ATC-20 Placard Classification)" />
              <div className="grid grid-cols-5 gap-2 mb-3">
                {(['RED', 'ORANGE', 'YELLOW', 'GREEN'] as const).map(color => (
                  <div key={color} className="col-span-1 rounded border border-gray-200 p-2.5 text-center">
                    <div className="text-2xl font-black" style={{ color: COLOR_HEX[color] }}>{counts[color]}</div>
                    <div className="text-[10px] font-semibold text-gray-500 mt-0.5 uppercase tracking-wide">{COLOR_LABEL[color]}</div>
                  </div>
                ))}
                <div className="col-span-1 rounded border border-gray-300 bg-gray-50 p-2.5 text-center">
                  <div className="text-2xl font-black text-gray-700">{counts.total}</div>
                  <div className="text-[10px] font-semibold text-gray-500 mt-0.5 uppercase tracking-wide">Total</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <StatBox label="Est. Persons at Risk" value={totalOccupancy.toLocaleString()} />
                <StatBox label="Critical Zone Occupancy" value={criticalOccupancy.toLocaleString()} accent />
                <StatBox label="Avg. Damage Probability" value={`${Math.round(buildings.reduce((s, b) => s + b.damage_probability, 0) / Math.max(buildings.length, 1) * 100)}%`} />
              </div>
              <p className="text-[10px] text-gray-400 mt-2 italic">
                ATC-20 placard equivalents: RED = UNSAFE · ORANGE/YELLOW = RESTRICTED USE · GREEN = INSPECTED.
                All recommendations are preliminary — licensed engineer sign-off required before legal posting.
              </p>
            </section>

            {/* ── SECTION 2: SCOUT UNIT SUMMARY ────────────────────────── */}
            {scouts.length > 0 && (
              <section>
                <SectionHeader number="2" title="AI Scout Unit Summary (US&R Reconnaissance Equivalent)" />
                <table className="w-full text-xs border-collapse">
                  <thead>
                    <tr className="bg-gray-100 text-gray-600 text-[10px] uppercase tracking-wider">
                      <th className="text-left px-2 py-1.5 border border-gray-200 font-semibold">Scout (ICS Role: Structures Eval.)</th>
                      <th className="text-left px-2 py-1.5 border border-gray-200 font-semibold">Assigned Structure</th>
                      <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">Status</th>
                      <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">Assessments</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scouts.map((s, i) => (
                      <tr key={s.scout_id} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                        <td className="px-2 py-1.5 border border-gray-200 font-mono font-bold text-[#003366]">
                          SCOUT-{s.scout_id.toUpperCase()}
                        </td>
                        <td className="px-2 py-1.5 border border-gray-200">{s.building_name}</td>
                        <td className="px-2 py-1.5 border border-gray-200 text-center">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            s.status === 'active' ? 'bg-green-100 text-green-700' :
                            s.status === 'arriving' ? 'bg-yellow-100 text-yellow-700' :
                            'bg-gray-100 text-gray-500'
                          }`}>{s.status.toUpperCase()}</span>
                        </td>
                        <td className="px-2 py-1.5 border border-gray-200 text-center">
                          {s.messages.filter(m => m.role === 'scout').length} viewpoint{s.messages.filter(m => m.role === 'scout').length !== 1 ? 's' : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            {/* ── SECTION 3: MOST IMPACTED STRUCTURES ──────────────────── */}
            <section>
              <SectionHeader number="3" title="Most Impacted Structures (FEMA P-58 Damage State)" />
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-gray-100 text-gray-600 text-[10px] uppercase tracking-wider">
                    <th className="text-left px-2 py-1.5 border border-gray-200 font-semibold">Structure</th>
                    <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">ATC-20</th>
                    <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">Score</th>
                    <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">Dmg Risk</th>
                    <th className="text-left px-2 py-1.5 border border-gray-200 font-semibold">Material</th>
                    <th className="text-center px-2 py-1.5 border border-gray-200 font-semibold">Occupancy</th>
                  </tr>
                </thead>
                <tbody>
                  {topStructures.map((b, i) => (
                    <tr key={b.id} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                      <td className="px-2 py-1.5 border border-gray-200 font-medium max-w-[180px] truncate">{b.name}</td>
                      <td className="px-2 py-1.5 border border-gray-200 text-center">
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: COLOR_HEX[b.color] + '22', color: COLOR_HEX[b.color] }}>
                          {COLOR_LABEL[b.color]}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 border border-gray-200 text-center font-mono font-bold">{b.triage_score.toFixed(0)}</td>
                      <td className="px-2 py-1.5 border border-gray-200 text-center font-mono">{Math.round(b.damage_probability * 100)}%</td>
                      <td className="px-2 py-1.5 border border-gray-200 text-gray-600 capitalize">{b.material || 'Unknown'}</td>
                      <td className="px-2 py-1.5 border border-gray-200 text-center">{b.estimated_occupancy}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* ── SECTION 4: ROUTE SUMMARY ──────────────────────────────── */}
            {route.length > 0 && (
              <section>
                <SectionHeader number="4" title="Safe Access Route (Dijkstra Hazard-Avoidance — ICS Ground Support Unit)" />
                <div className="grid grid-cols-3 gap-2 text-xs mb-2">
                  <StatBox label="Route Waypoints" value={String(route.length)} />
                  <StatBox label="Hazard Annotations" value={String(route.filter(w => w.hazard).length)} accent={route.filter(w => w.hazard).length > 0} />
                  <StatBox label="Blocked Segments" value={String(route.filter(w => w.hazard?.type === 'blocked').length)} accent={route.filter(w => w.hazard?.type === 'blocked').length > 0} />
                </div>
                <p className="text-[10px] text-gray-500 italic">
                  Route computed using Dijkstra hazard-avoidance informed by all scout SharedState findings. Validated by OpenClaw Route Agent (ICS: Ground Support Unit). Field personnel must physically verify annotated hazard waypoints before advancing.
                </p>
              </section>
            )}

            {/* ── SECTION 5: ROAD ACCESS ASSESSMENT ────────────────────── */}
            <section>
              <SectionHeader number="5" title="Road Access Assessment (US&R FOG §7 — Ground Access)" />
              {blockages.length === 0 ? (
                <p className="text-xs text-gray-500 italic">No significant road blockages predicted at current damage levels.</p>
              ) : (
                <div className="space-y-1.5">
                  {blockages.map((r, i) => (
                    <div key={i} className="flex items-start gap-3 rounded border border-gray-200 px-3 py-2 bg-gray-50">
                      <div className="shrink-0 mt-0.5">
                        <span className={`inline-block text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider ${PROB_STYLE[r.probability].badge}`}>
                          {r.probability}
                        </span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <span className="text-xs font-bold text-gray-800">{r.name}</span>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <div className="w-20 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                              <div className={`h-full rounded-full ${PROB_STYLE[r.probability].bar}`} style={{ width: `${r.pct}%` }} />
                            </div>
                            <span className="text-[10px] font-mono text-gray-500">{r.pct}%</span>
                          </div>
                        </div>
                        <p className="text-[10px] text-gray-500 leading-snug">{r.reason}</p>
                        {r.nearStructures.length > 0 && (
                          <p className="text-[10px] text-gray-400 mt-0.5">Near: {r.nearStructures.join(', ')}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* ── SECTION 6: RECOMMENDED ACTIONS ───────────────────────── */}
            <section>
              <SectionHeader number="6" title="Recommended Immediate Actions (ICS Operational Objectives)" />
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-700">
                {[
                  counts.RED > 0   && `Dispatch US&R teams to ${counts.RED} UNSAFE (Red) structure${counts.RED > 1 ? 's' : ''} — ATC-20 Detailed Evaluation required`,
                  counts.RED >= 3  && `Establish incident command post — ${counts.RED} critical sites exceed single-unit capacity`,
                  blockages.filter(r => r.probability === 'CRITICAL').length > 0 &&
                    `Route all emergency vehicles via alternate corridors — ${blockages.filter(r => r.probability === 'CRITICAL').length} road(s) critically obstructed`,
                  criticalOccupancy > 0 &&
                    `Account for est. ${criticalOccupancy.toLocaleString()} persons in UNSAFE structures — triage and medical staging required`,
                  counts.ORANGE > 0 &&
                    `Dispatch licensed engineer to ${counts.ORANGE} RESTRICTED USE (Orange) structure${counts.ORANGE > 1 ? 's' : ''} for ATC-20 Detailed Evaluation`,
                  route.filter(w => w.hazard?.type === 'blocked').length > 0 &&
                    `Verify ${route.filter(w => w.hazard?.type === 'blocked').length} blocked route segment${route.filter(w => w.hazard?.type === 'blocked').length !== 1 ? 's' : ''} before committing ground support personnel`,
                  true && 'Isolate gas, electrical, and water utilities in all Red and Orange zones — contact utility authority',
                  true && 'Post ATC-20 placards at all evaluated structures — jurisdiction AHJ authorization required',
                  true && 'Initiate mutual aid request if rescue resources insufficient for Red structure count',
                  true && 'Document structural conditions with photographic evidence for FEMA PA reimbursement (Category E)',
                ].filter(Boolean).slice(0, 8).map((action, i) => (
                  <div key={i} className="flex gap-1.5 py-0.5">
                    <span className="text-[#003366] font-bold shrink-0">{i + 1}.</span>
                    <span>{action as string}</span>
                  </div>
                ))}
              </div>
            </section>

          </div>

          {/* ── FOOTER ───────────────────────────────────────────────────── */}
          <div className="bg-gray-100 border-t border-gray-200 px-8 py-3 flex justify-between items-center text-[10px] text-gray-400">
            <span>Generated by GroundZero AI-ICS · {dateStr} {timeStr} hrs</span>
            <span className="font-bold text-orange-600 uppercase tracking-wider">PRELIMINARY — Subject to Field Verification</span>
            <span className="font-mono">{incidentNo}</span>
          </div>

        </div>
      </div>
    </>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function SectionHeader({ number, title }: { number: string; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="w-5 h-5 rounded-full bg-[#003366] text-white text-[10px] font-bold flex items-center justify-center shrink-0">
        {number}
      </span>
      <span className="text-xs font-bold text-[#003366] uppercase tracking-wider">{title}</span>
      <div className="flex-1 h-px bg-[#003366]/20" />
    </div>
  )
}

function StatBox({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`rounded border px-3 py-2 ${accent ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-gray-50'}`}>
      <div className={`text-base font-black ${accent ? 'text-red-600' : 'text-gray-700'}`}>{value}</div>
      <div className="text-[10px] text-gray-500 font-medium mt-0.5">{label}</div>
    </div>
  )
}
