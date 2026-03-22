'use client'

import { useEffect, useRef, useState } from 'react'
import type { Scout, ChatMessage, Severity } from '../_lib/types'

const SEVERITY_COLORS: Record<Severity, string> = {
  CRITICAL: 'text-red-400',
  MODERATE: 'text-blue-400',
  LOW: 'text-yellow-400',
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

export default function ScoutPanel({ scout, isActive, onFocus, onMessage, onRequestRoute, onClose }: Props) {
  const [input, setInput] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [scout.messages])

  function handleSend() {
    const text = input.trim()
    if (!text) return
    onMessage(scout.scout_id, text)
    setInput('')
    inputRef.current?.focus()
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

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

      {/* Latest viewpoint image */}
      {scout.messages.length > 0 && getLatestImage(scout.messages) && (
        <div className="relative border-b border-white/[0.06]">
          <img
            src={`data:image/jpeg;base64,${getLatestImage(scout.messages)}`}
            alt="Scout viewpoint"
            className="w-full h-[180px] object-cover"
          />
          {scout.viewpoint && (
            <div className="absolute bottom-2 left-2 text-[10px] font-mono text-white bg-black/60 px-2 py-0.5 rounded">
              {scout.viewpoint.facing} · {scout.viewpoint.heading.toFixed(0)}°
            </div>
          )}
        </div>
      )}

      {/* Chat messages */}
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

      {/* Input */}
      <div className="border-t border-white/[0.06] p-3 flex items-end gap-2">
        <textarea
          ref={inputRef}
          className="flex-1 resize-none bg-white/5 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500/40 leading-relaxed"
          rows={2}
          placeholder={`Ask ${scout.scout_id.toUpperCase()}… (Enter to send)`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={scout.status === 'arriving'}
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || scout.status === 'arriving'}
          className="p-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  )
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isCommander = msg.role === 'commander'

  if (isCommander) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-xl rounded-tr-sm bg-blue-600/20 border border-blue-500/20 px-3 py-2">
          <p className="text-xs text-blue-200 font-mono">{msg.text}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5">
      {/* Scout narrative */}
      <div className="rounded-xl rounded-tl-sm bg-white/5 border border-white/[0.06] px-3 py-2">
        <p className="text-xs text-slate-300 font-mono leading-relaxed whitespace-pre-wrap">{msg.text}</p>
      </div>

      {/* Findings */}
      {msg.analysis && msg.analysis.findings.length > 0 && (
        <div className="space-y-1">
          {msg.analysis.findings.map((f, i) => (
            <div key={i} className="flex items-start gap-2 text-[10px] font-mono">
              <span className={`font-bold shrink-0 ${SEVERITY_COLORS[f.severity]}`}>[{f.severity}]</span>
              <span className="text-slate-400">{f.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* Recommended action */}
      {msg.analysis?.recommended_action && (
        <div className="text-[10px] font-mono text-slate-500 italic pl-1">
          → {msg.analysis.recommended_action}
        </div>
      )}
    </div>
  )
}

function getLatestImage(messages: ChatMessage[]): string | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].image_b64) return messages[i].image_b64
  }
  return undefined
}
