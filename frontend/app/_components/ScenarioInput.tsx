'use client'

import { useState } from 'react'
import type { WsStatus } from '../_lib/useWebSocket'

const PRESET_PROMPT = '7.2 magnitude earthquake, epicenter at The Pylons, 2:30 PM Tuesday'

interface Props {
  wsStatus: WsStatus
  onSubmit: (prompt: string, radius_m: number) => void
  center: { lat: number; lng: number }
  disabled: boolean
}

const statusColors: Record<WsStatus, string> = {
  connecting: 'bg-yellow-500',
  connected: 'bg-green-500',
  disconnected: 'bg-red-500',
}

const statusLabels: Record<WsStatus, string> = {
  connecting: 'Connecting…',
  connected: 'Connected',
  disconnected: 'Disconnected — retrying',
}

export default function ScenarioInput({ wsStatus, onSubmit, center, disabled }: Props) {
  const [prompt, setPrompt] = useState('')

  function handleSubmit() {
    const text = prompt.trim() || PRESET_PROMPT
    onSubmit(text, 1000)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="absolute bottom-0 left-0 right-0 flex items-end justify-center pb-6 px-4 pointer-events-none z-20">
      <div
        className="w-full max-w-2xl rounded-2xl border border-white/10 bg-[rgba(10,12,20,0.92)] backdrop-blur-md shadow-2xl pointer-events-auto"
        style={{ boxShadow: '0 0 40px rgba(0,0,0,0.6)' }}
      >
        {/* Status bar */}
        <div className="flex items-center gap-2 px-4 pt-3 pb-1">
          <span className={`h-2 w-2 rounded-full ${statusColors[wsStatus]} ${wsStatus === 'connecting' ? 'arriving-pulse' : ''}`} />
          <span className="text-xs text-slate-400 font-mono">{statusLabels[wsStatus]}</span>
          <span className="text-xs text-slate-500 font-mono">
            Center {center.lat.toFixed(5)}, {center.lng.toFixed(5)}
          </span>
          <span className="ml-auto text-xs text-slate-600 font-mono">GroundZero · COMMAND INTERFACE</span>
        </div>

        {/* Input row */}
        <div className="flex items-end gap-3 p-3 pt-1">
          <textarea
            className="flex-1 resize-none bg-transparent text-sm text-slate-200 placeholder-slate-500 focus:outline-none font-mono leading-relaxed"
            rows={2}
            placeholder={`e.g. "${PRESET_PROMPT}"`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
          />

          <div className="flex flex-col gap-2 items-end">
            <button
              onClick={() => {
                setPrompt(PRESET_PROMPT)
              }}
              className="text-xs text-slate-500 hover:text-slate-300 transition-colors whitespace-nowrap"
              type="button"
            >
              Use VT Demo Prompt
            </button>
            <button
              onClick={handleSubmit}
              disabled={disabled || wsStatus !== 'connected'}
              className="flex items-center gap-2 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 px-4 py-2 text-sm font-semibold text-white transition-colors"
              type="button"
            >
              <span>Deploy</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
