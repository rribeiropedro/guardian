'use client'

import { useEffect, useRef } from 'react'
import type { Scout, ChatMessage, Severity } from '../_lib/types'

const SEVERITY_COLORS: Record<Severity, string> = {
  CRITICAL: 'text-red-400',
  MODERATE: 'text-orange-400',
  LOW: 'text-yellow-400',
}

const SEVERITY_BG: Record<Severity, string> = {
  CRITICAL: 'bg-red-500/10 border-red-500/20',
  MODERATE: 'bg-orange-500/10 border-orange-500/20',
  LOW: 'bg-yellow-500/10 border-yellow-500/20',
}

const RISK_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-500/20 text-red-400 border-red-500/30',
  MODERATE: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  LOW: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
}

const STATUS_LABELS: Record<Scout['status'], string> = {
  arriving: 'ARRIVING',
  active: 'ACTIVE',
  idle: 'STANDBY',
}

const STATUS_COLORS: Record<Scout['status'], string> = {
  arriving: 'text-yellow-400',
  active: 'text-green-400',
  idle: 'text-slate-400',
}

interface Props {
  scout: Scout
  isActive: boolean
  onFocus: () => void
  onMessage: (scoutId: string, message: string) => void
  onRequestRoute: (buildingId: string) => void
  onClose: () => void
}

export default function ScoutPanel({ scout, isActive, onFocus, onRequestRoute, onClose }: Props) {
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [scout.messages])

  return (
    <div
      className={`scout-panel-enter flex flex-col w-full h-full rounded-xl border transition-colors shadow-2xl ${
        isActive ? 'border-blue-500/40 bg-[rgba(8,12,24,0.95)]' : 'border-white/[0.06] bg-[rgba(8,10,18,0.9)]'
      } backdrop-blur-md`}
      onClick={onFocus}
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
        <div className="flex flex-col flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-bold tracking-widest text-slate-300 uppercase">
              SCOUT-{scout.scout_id.toUpperCase()}
            </span>
            <span className={`text-[10px] font-mono font-bold tracking-wider ${STATUS_COLORS[scout.status]} ${scout.status === 'arriving' ? 'arriving-pulse' : ''}`}>
              {STATUS_LABELS[scout.status]}
            </span>
          </div>
          <span className="text-xs text-slate-500 truncate font-mono">{scout.building_name}</span>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={(e) => { e.stopPropagation(); onRequestRoute(scout.building_id) }}
            className="text-[10px] font-mono tracking-wider px-2 py-1 rounded border border-blue-500/30 text-blue-400 hover:bg-blue-500/10 transition-colors"
            title="Walk Route to this building"
          >
            ROUTE
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onClose() }}
            className="ml-1 text-slate-600 hover:text-slate-300 transition-colors p-1"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Reports */}
      <div className="flex-1 overflow-y-auto chat-scroll px-3 py-3 space-y-3">
        {scout.messages.length === 0 && (
          <div className="text-center text-xs text-slate-600 font-mono mt-8 arriving-pulse">
            Scout deploying…
          </div>
        )}

        {scout.messages.map((msg, i) => (
          <ChatBubble key={i} msg={msg} />
        ))}

        <div ref={chatEndRef} />
      </div>
    </div>
  )
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'commander') {
    // Commander messages are not shown — no user input in read-only mode
    return null
  }

  const analysis = msg.analysis
  const riskLevel = analysis?.risk_level as Severity | undefined

  return (
    <div className="rounded-xl border border-white/[0.07] bg-white/[0.03] overflow-hidden">
      {/* SITREP header row */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.06] bg-white/[0.02]">
        <span className="text-[9px] font-mono font-bold tracking-widest text-slate-500 uppercase">SITREP</span>
        {riskLevel && (
          <span className={`text-[9px] font-mono font-bold tracking-wider px-1.5 py-0.5 rounded border ${RISK_BADGE[riskLevel] ?? RISK_BADGE.MODERATE}`}>
            {riskLevel}
          </span>
        )}
        {analysis?.approach_viable !== undefined && (
          <span className={`ml-auto text-[9px] font-mono font-bold tracking-wider ${analysis.approach_viable ? 'text-emerald-400' : 'text-red-400'}`}>
            {analysis.approach_viable ? '✓ APPROACH VIABLE' : '✗ APPROACH BLOCKED'}
          </span>
        )}
      </div>

      {/* Narrative */}
      {msg.text && (
        <div className="px-3 py-2.5 border-b border-white/[0.05]">
          <p className="text-[11px] text-slate-300 font-mono leading-relaxed whitespace-pre-wrap">{msg.text}</p>
        </div>
      )}

      {/* Findings */}
      {analysis && analysis.findings.length > 0 && (
        <div className="px-3 py-2 border-b border-white/[0.05]">
          <span className="text-[9px] font-mono font-bold tracking-widest text-slate-600 uppercase block mb-1.5">
            Findings ({analysis.findings.length})
          </span>
          <div className="space-y-1.5">
            {analysis.findings.map((f, i) => (
              <div key={i} className={`flex items-start gap-2 rounded-lg border px-2 py-1.5 ${SEVERITY_BG[f.severity] ?? SEVERITY_BG.MODERATE}`}>
                <span className={`text-[9px] font-mono font-bold shrink-0 mt-px uppercase ${SEVERITY_COLORS[f.severity]}`}>
                  {f.severity}
                </span>
                <div className="flex-1 min-w-0">
                  <span className="text-[9px] font-mono text-slate-500 uppercase tracking-wider block mb-0.5">
                    {f.category}
                  </span>
                  <p className="text-[10px] font-mono text-slate-300 leading-snug">{f.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommended action */}
      {analysis?.recommended_action && (
        <div className="px-3 py-2">
          <span className="text-[9px] font-mono font-bold tracking-widest text-slate-600 uppercase block mb-1">
            Action
          </span>
          <p className="text-[10px] font-mono text-slate-400 leading-snug">{analysis.recommended_action}</p>
        </div>
      )}
    </div>
  )
}
