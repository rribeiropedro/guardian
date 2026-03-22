import Link from 'next/link'
import { Playfair_Display } from 'next/font/google'

const playfair = Playfair_Display({ subsets: ['latin'], variable: '--font-playfair' })

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
      <div className="flex-1 flex items-center px-12 lg:px-20">
        <div className="w-full flex flex-col lg:flex-row gap-12 lg:gap-20 items-center">

          {/* Left */}
          <div className="flex-1 max-w-2xl">
            <div className="inline-flex items-center gap-2.5 border border-slate-700/60 rounded-full px-4 py-1.5 text-[11px] tracking-[0.2em] text-slate-500 mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.8)]" />
              SYSTEM V2.4 OPERATIONAL
            </div>

            <h1
              className="text-[clamp(3rem,6vw,5.5rem)] leading-[1.05] tracking-tight mb-7"
              style={{ fontFamily: 'var(--font-playfair)' }}
            >
              The art of<br />
              <em className="text-slate-500 not-italic" style={{ fontFamily: 'var(--font-playfair)', fontStyle: 'italic' }}>
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

          {/* Right — mock dashboard */}
          <div className="flex-1 flex justify-center lg:justify-end max-w-xl w-full">
            <div className="relative w-full max-w-lg">
              {/* Background panel */}
              <div className="absolute inset-0 bg-gradient-to-br from-slate-800/30 to-transparent rounded-sm -z-10 blur-2xl" />

              {/* Main window */}
              <div className="border border-slate-700/50 bg-[#0d0f1a] rounded-sm overflow-hidden shadow-2xl">
                {/* Titlebar */}
                <div className="flex items-center gap-1.5 px-4 py-3 border-b border-slate-700/40 bg-[#0a0c14]">
                  <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
                  <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
                  <span className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
                  <span className="ml-3 text-[11px] font-mono text-slate-500">triage.live</span>
                </div>

                {/* Building triage list */}
                <div className="p-5 space-y-2.5 font-mono text-xs">
                  {[
                    { id: 'B-01', name: 'Squires Student Center', color: 'RED', scouts: 2, score: 94 },
                    { id: 'B-02', name: 'Patton Hall',            color: 'ORANGE', scouts: 1, score: 71 },
                    { id: 'B-03', name: 'Newman Library',         color: 'YELLOW', scouts: 1, score: 48 },
                    { id: 'B-04', name: 'Surge Space',            color: 'GREEN',  scouts: 0, score: 22 },
                  ].map((b) => (
                    <div key={b.id} className="flex items-center gap-3 py-2 px-3 bg-slate-800/30 border border-slate-700/30 rounded-sm">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ backgroundColor: colorHex(b.color) }}
                      />
                      <span className="text-slate-500 w-10">{b.id}</span>
                      <span className="text-slate-300 flex-1 truncate">{b.name}</span>
                      <span className="text-slate-500">{b.scouts > 0 ? `${b.scouts} scout${b.scouts > 1 ? 's' : ''}` : '—'}</span>
                      <span className="text-slate-400 w-6 text-right">{b.score}</span>
                    </div>
                  ))}
                </div>

                {/* Floating scout status card */}
                <div className="mx-5 mb-5 border border-slate-700/50 bg-[#111320] p-4 rounded-sm">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-[11px] text-slate-500 tracking-widest">SCOUT ALPHA — LIVE</span>
                    <span className="text-[11px] text-green-400 font-mono">● TRANSMITTING</span>
                  </div>
                  <p className="text-slate-400 text-[11px] leading-relaxed">
                    Structural damage observed on floors 2–4. Stairwell B blocked.
                    Recommending entry via north corridor. Hazard: gas leak suspected.
                  </p>
                </div>
              </div>

              {/* Floating hazard badge */}
              <div className="absolute -bottom-4 -left-6 border border-red-900/50 bg-[#120a0a] px-4 py-2.5 rounded-sm shadow-xl">
                <div className="text-[10px] text-slate-500 tracking-widest mb-1">HAZARD DETECTED</div>
                <div className="text-red-400 text-xs font-mono font-semibold">▲ 3 HIGH-RISK ZONES</div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}

function colorHex(color: string) {
  return { RED: '#ef4444', ORANGE: '#f97316', YELLOW: '#eab308', GREEN: '#22c55e' }[color] ?? '#64748b'
}
