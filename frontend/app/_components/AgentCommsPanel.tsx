'use client'

import { useEffect, useRef, useState } from 'react'
import type { AgentFeedEntry, Scout } from '../_lib/types'
import ScoutPanel from './ScoutPanel'

// Per-scout callsign colors
const SCOUT_COLORS: Record<string, string> = {
  alpha: 'text-emerald-400',
  bravo: 'text-cyan-400',
  charlie: 'text-violet-400',
  delta: 'text-orange-400',
}

function scoutColor(scoutId: string): string {
  return SCOUT_COLORS[scoutId.toLowerCase()] ?? 'text-slate-300'
}

interface Props {
  scouts: Scout[]
  feed: AgentFeedEntry[]
  onMessage: (scoutId: string, message: string) => void
  onRequestRoute: (buildingId: string) => void
  routeReady: boolean
}

export default function AgentCommsPanel({ scouts, feed, onMessage, onRequestRoute, routeReady }: Props) {
  const [isOpen, setIsOpen] = useState(false)
  const [activeTab, setActiveTab] = useState('ALL')
  const [lastSeenCount, setLastSeenCount] = useState(0)

  const unreadCount = isOpen ? 0 : Math.max(0, feed.length - lastSeenCount)

  // Auto-open when first scout deploys
  useEffect(() => {
    if (scouts.length > 0 && !isOpen) {
      setIsOpen(true)
      setLastSeenCount(feed.length)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scouts.length])

  function openPanel() {
    setIsOpen(true)
    setLastSeenCount(feed.length)
  }

  function closePanel() {
    setIsOpen(false)
    setLastSeenCount(feed.length)
  }

  const activeScout = activeTab === 'ALL' ? null : scouts.find(s => s.scout_id === activeTab)

  return (
    <>
      {/* Toggle button — bottom-right corner */}
      <button
        onClick={isOpen ? closePanel : openPanel}
        className="fixed bottom-6 right-6 z-30 flex items-center gap-2 px-3 py-2.5 rounded-xl
          bg-[rgba(8,10,18,0.95)] border border-blue-500/30 text-blue-400
          hover:border-blue-400/60 hover:text-blue-300 transition-all shadow-lg backdrop-blur-md"
        title="Toggle Agent Comms"
      >
        {/* Radio tower icon */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M5 12.55a11 11 0 0 1 14.08 0" />
          <path d="M1.42 9a16 16 0 0 1 21.16 0" />
          <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
          <line x1="12" y1="20" x2="12" y2="20" strokeLinecap="round" strokeWidth="3" />
        </svg>
        <span className="text-[11px] font-mono font-bold tracking-widest">COMMS</span>
        {unreadCount > 0 && (
          <span className="flex items-center justify-center h-4 w-4 rounded-full bg-red-500 text-[9px] font-mono font-bold text-white">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Panel — slides in from right */}
      <div
        className={`fixed right-0 top-0 bottom-0 w-[380px] z-20 flex flex-col
          bg-[rgba(6,8,16,0.98)] border-l border-white/[0.07] backdrop-blur-xl
          transition-transform duration-300 ease-in-out shadow-2xl
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.07] shrink-0">
          <div className="flex items-center gap-2">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-400">
              <path d="M5 12.55a11 11 0 0 1 14.08 0" />
              <path d="M1.42 9a16 16 0 0 1 21.16 0" />
              <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
              <line x1="12" y1="20" x2="12" y2="20" strokeLinecap="round" strokeWidth="3" />
            </svg>
            <span className="text-[11px] font-mono font-bold tracking-widest text-slate-200">
              AGENT COMMS
            </span>
            {scouts.length > 0 && (
              <span className="text-[10px] font-mono text-slate-600 border-l border-white/10 pl-2">
                {scouts.length} unit{scouts.length !== 1 ? 's' : ''} on station
              </span>
            )}
          </div>
          <button
            onClick={closePanel}
            className="text-slate-600 hover:text-slate-300 transition-colors p-1"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-white/[0.07] px-1 shrink-0 overflow-x-auto">
          <TabButton
            label="ALL"
            active={activeTab === 'ALL'}
            onClick={() => setActiveTab('ALL')}
          />
          {scouts.map(s => (
            <TabButton
              key={s.scout_id}
              label={s.scout_id.toUpperCase()}
              active={activeTab === s.scout_id}
              onClick={() => setActiveTab(s.scout_id)}
              status={s.status}
              color={scoutColor(s.scout_id)}
            />
          ))}
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-hidden flex flex-col">
          {activeTab === 'ALL' ? (
            <UnifiedFeed feed={feed} />
          ) : activeScout ? (
            <ScoutPanel
              scout={activeScout}
              isActive={true}
              onFocus={() => {}}
              onMessage={onMessage}
              onRequestRoute={onRequestRoute}
              onClose={() => setActiveTab('ALL')}
              routeReady={routeReady}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-xs font-mono text-slate-600">
              No scout on this channel
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

interface TabProps {
  label: string
  active: boolean
  onClick: () => void
  status?: Scout['status']
  color?: string
}

function TabButton({ label, active, onClick, status, color }: TabProps) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-2 text-[10px] font-mono font-bold tracking-wider
        border-b-2 transition-colors whitespace-nowrap
        ${active
          ? 'border-blue-500 text-slate-200'
          : 'border-transparent text-slate-600 hover:text-slate-400 hover:border-white/20'
        }`}
    >
      {status && (
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            status === 'active' ? 'bg-emerald-400' :
            status === 'arriving' ? 'bg-yellow-400 arriving-pulse' :
            'bg-slate-600'
          }`}
        />
      )}
      <span className={active && color ? color : undefined}>{label}</span>
    </button>
  )
}

// ── Unified Feed ──────────────────────────────────────────────────────────────

function UnifiedFeed({
  feed,
}: {
  feed: AgentFeedEntry[]
}) {
  const feedEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  // Start true so the first batch of messages scrolls into view automatically.
  const isAtBottomRef = useRef(true)

  function onScroll() {
    const el = scrollContainerRef.current
    if (!el) return
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
  }

  // Only autoscroll when the user is already at (or near) the bottom.
  useEffect(() => {
    if (isAtBottomRef.current) {
      feedEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [feed])

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div
        ref={scrollContainerRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto chat-scroll px-3 py-3 space-y-2.5"
      >
        {feed.length === 0 && (
          <div className="text-center text-[11px] text-slate-600 font-mono mt-10 arriving-pulse">
            Awaiting agent deployment…
          </div>
        )}

        {feed.map(entry => (
          <FeedEntry key={entry.id} entry={entry} />
        ))}

        <div ref={feedEndRef} />
      </div>
    </div>
  )
}

// ── Feed entry renderer ───────────────────────────────────────────────────────

function FeedEntry({ entry }: { entry: AgentFeedEntry }) {
  const time = new Date(entry.timestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })

  // Extract scout_id from callsign label like "SCOUT-ALPHA"
  const scoutIdFromLabel = (label: string) =>
    label.replace('SCOUT-', '').toLowerCase()

  if (entry.entryType === 'status') {
    return (
      <div className="flex items-center gap-2 py-0.5">
        <span className="text-[10px] font-mono text-slate-700 shrink-0">{time}</span>
        <span className={`text-[10px] font-mono font-bold shrink-0 ${scoutColor(scoutIdFromLabel(entry.from))}`}>
          {entry.from}
        </span>
        <span className="text-[10px] font-mono text-slate-500">{entry.text}</span>
      </div>
    )
  }

  if (entry.entryType === 'commander') {
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-slate-700">{time}</span>
          <span className="text-[10px] font-mono text-slate-500">
            CMD → {entry.to}
          </span>
        </div>
        <div className="max-w-[85%] rounded-xl rounded-tr-sm bg-blue-600/20 border border-blue-500/20 px-3 py-2">
          <p className="text-xs font-mono text-blue-200">{entry.text}</p>
        </div>
      </div>
    )
  }

  if (entry.entryType === 'cross_ref') {
    return (
      <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[9px] font-mono font-bold tracking-widest text-amber-400/80 uppercase">
            Cross-Ref
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            {entry.from}
            {entry.to ? ` → ${entry.to}` : ''}
          </span>
          <span className="text-[10px] font-mono text-slate-700 ml-auto">{time}</span>
        </div>
        <p className="text-[11px] font-mono text-amber-200/75 leading-relaxed">{entry.text}</p>
      </div>
    )
  }

  // sitrep / streaming
  const fromColor = scoutColor(scoutIdFromLabel(entry.from))
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-[10px] font-mono font-bold ${fromColor}`}>{entry.from}</span>
        <span className="text-[10px] font-mono text-slate-700">{time}</span>
        {entry.isStreaming && (
          <span className="text-[10px] font-mono text-slate-600 italic">transmitting…</span>
        )}
      </div>
      <div className="rounded-lg bg-white/[0.03] border border-white/[0.05] px-3 py-2">
        <p className="text-[11px] font-mono text-slate-300 leading-relaxed whitespace-pre-wrap break-words">
          {entry.text || <span className="text-slate-700">…</span>}
          {entry.isStreaming && (
            <span className="inline-block w-[6px] h-[12px] bg-slate-400 ml-0.5 animate-pulse align-text-bottom" />
          )}
        </p>
      </div>
    </div>
  )
}
