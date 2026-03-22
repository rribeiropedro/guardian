'use client'

import Link from 'next/link'
import dynamic from 'next/dynamic'
import { Playfair_Display } from 'next/font/google'

const playfair = Playfair_Display({ subsets: ['latin'], variable: '--font-playfair' })
const MapPreview = dynamic(() => import('./_components/MapPreview'), { ssr: false })

export default function LandingPage() {
  return (
    <div className={`${playfair.variable} h-full bg-[#0a0a0f] text-slate-200 flex flex-col overflow-hidden`}>
      {/* Logo — no navbar */}
      <div className="px-8 pt-7 pb-0 flex items-center gap-2.5">
        <div className="w-8 h-8 border border-slate-600 flex items-center justify-center text-[11px] font-mono text-slate-400">
          {'</>'}
        </div>
        <span className="text-sm font-semibold tracking-widest text-slate-300 uppercase">GroundZero</span>
      </div>

      {/* Hero */}
      <div className="flex-1 flex items-center px-12 lg:px-20 overflow-hidden">
        <div className="w-full flex flex-col lg:flex-row gap-12 lg:gap-20 items-center h-full py-10">

          {/* Left */}
          <div className="flex-1 max-w-2xl shrink-0">
            <div className="inline-flex items-center gap-2.5 border border-slate-700/60 rounded-full px-4 py-1.5 text-[11px] tracking-[0.2em] text-slate-500 mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.8)]" />
              SYSTEM V2.4 OPERATIONAL
            </div>

            <h1
              className="text-[clamp(3rem,6vw,5.5rem)] leading-[1.05] tracking-tight mb-7"
              style={{ fontFamily: 'var(--font-playfair)' }}
            >
              The art of<br />
              <em style={{ fontFamily: 'var(--font-playfair)', fontStyle: 'italic', color: 'rgb(100 116 139)' }}>
                precision
              </em>{' '}
              response.
            </h1>

            <p className="text-slate-400 text-[1.05rem] leading-relaxed mb-10 max-w-md">
              Deploy AI scouts to critical buildings the moment disaster strikes.
              GroundZero delivers real-time triage, hazard-aware routing, and
              first-responder intelligence — all from a single command.
            </p>

            <Link
              href="/command-center"
              className="inline-flex items-center gap-3 bg-slate-200 text-slate-900 px-7 py-3.5 text-sm font-semibold tracking-wide hover:bg-white transition-colors"
            >
              Enter Command Center
              <span className="text-base">→</span>
            </Link>
          </div>

          {/* Right — 3D map */}
          <div className="flex-1 h-full min-h-[400px] relative rounded-sm overflow-hidden border border-slate-700/40 shadow-2xl flex flex-col">
            {/* Titlebar */}
            <div className="flex items-center gap-1.5 px-4 py-3 border-b border-slate-700/40 bg-[#0a0c14] shrink-0 z-10">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
              <span className="ml-3 text-[11px] font-mono text-slate-500">triage.live</span>
            </div>
            <div className="flex-1 relative">
            <MapPreview />
            {/* Gradient fade on left edge to blend with background */}
            <div className="absolute inset-y-0 left-0 w-16 bg-gradient-to-r from-[#0a0a0f] to-transparent pointer-events-none" />
            <div className="absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-[#0a0a0f]/40 to-transparent pointer-events-none" />
            {/* Bottom label */}
            <div className="absolute bottom-4 left-4 text-[10px] font-mono text-slate-500 tracking-widest">
              NEW YORK CITY · LIVE TRIAGE VIEW
            </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}
