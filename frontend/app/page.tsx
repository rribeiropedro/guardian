"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { Playfair_Display, Space_Grotesk } from "next/font/google";
import { useRouter } from "next/navigation";
import { useRef, useCallback } from "react";

const playfair = Playfair_Display({
  subsets: ["latin"],
  variable: "--font-playfair",
});
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space",
});
const MapPreview = dynamic(() => import("./_components/MapPreview"), {
  ssr: false,
});

const STEPS = [
  {
    label: "01",
    title: "Describe the disaster",
    body: "Type a scenario — magnitude, location, time of day. GroundZero parses it instantly and anchors the epicenter on the live map.",
  },
  {
    label: "02",
    title: "AI scouts fan out",
    body: "Autonomous agents deploy to the highest-risk buildings. Each scout streams real-time triage scores, structural hazards, and occupancy estimates back to command.",
  },
  {
    label: "03",
    title: "Walk the ground",
    body: "Switch to first-person mode. A road-following route connects every critical stop — each dot tells you why it matters before you arrive.",
  },
];

const CAPABILITIES = [
  {
    icon: (
      <svg
        width="28"
        height="28"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      >
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" />
        <path d="M2 12l10 5 10-5" />
      </svg>
    ),
    title: "K-means triage clustering",
    body: "Buildings are grouped spatially by impact score. The highest-damage cluster gets the route — or all red zones get connected if severity is extreme.",
  },
  {
    icon: (
      <svg
        width="28"
        height="28"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      >
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
      </svg>
    ),
    title: "Real-time VLM analysis",
    body: "Claude vision models inspect Street View imagery at each waypoint — identifying blocked access, structural damage, and overhead hazards.",
  },
  {
    icon: (
      <svg
        width="28"
        height="28"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      >
        <path d="M3 12h18M3 6h18M3 18h18" />
        <rect x="9" y="9" width="6" height="6" rx="1" />
      </svg>
    ),
    title: "Road-following routes",
    body: "Mapbox Directions snaps every stop to the nearest drivable road. No cutting through buildings — routes that responders can actually drive.",
  },
];

export default function LandingPage() {
  const router = useRouter();
  const rootRef = useRef<HTMLDivElement>(null);

  const navigateTo = useCallback((href: string) => {
    const el = rootRef.current;
    if (!el) { router.push(href); return; }
    el.classList.remove("page-enter");
    el.classList.add("page-exit");
    setTimeout(() => router.push(href), 420);
  }, [router]);

  return (
    <div
      ref={rootRef}
      className={`${playfair.variable} ${spaceGrotesk.variable} text-slate-200 flex flex-col overflow-y-auto h-full page-enter`}
      style={{ background: "radial-gradient(ellipse 70% 50% at 50% 50%, #0d1424 0%, #0a0a0f 70%)" }}
    >
      {/* ── Logo ── */}
      <div className="px-8 pt-7 pb-0 flex items-center gap-2.5 shrink-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/helmet-logo.svg"
          alt="GroundZero"
          className="w-8 h-8 object-contain"
          style={{ filter: "drop-shadow(0 0 8px rgba(59,130,246,0.55))" }}
        />
        <span className="text-sm font-semibold tracking-widest text-slate-300 uppercase">
          GroundZero
        </span>
      </div>

      {/* ── Hero ── */}
      <div
        className="relative flex-none flex items-center px-12 lg:px-20"
        style={{ minHeight: "calc(100vh - 56px)" }}
      >
        <div className="w-full flex flex-col lg:flex-row gap-12 lg:gap-20 items-center py-10">
          {/* Left */}
          <div className="flex-1 max-w-2xl shrink-0 pl-8 lg:pl-16">
            <div className="inline-flex items-center gap-2.5 border border-slate-700/60 rounded-full px-4 py-1.5 text-[11px] tracking-[0.2em] text-slate-500 mb-8">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.8)]" />
              SYSTEM V2.4 OPERATIONAL
            </div>

            <h1
              className="text-[clamp(3rem,6vw,5.5rem)] leading-[1.05] tracking-tight mb-7"
              style={{ fontFamily: "var(--font-playfair)" }}
            >
              The art of
              <br />
              <em
                style={{
                  fontFamily: "var(--font-playfair)",
                  fontStyle: "italic",
                  color: "#3B82F6",
                  textShadow: "0 0 40px rgba(59,130,246,0.25)",
                }}
              >
                precision
              </em>{" "}
              response.
            </h1>

            <p className="text-slate-400 text-[1.05rem] leading-relaxed mb-10 max-w-md">
              Deploy AI scouts to critical buildings the moment disaster
              strikes. GroundZero delivers real-time triage, hazard-aware
              routing, and first-responder intelligence — all from a single
              command.
            </p>

            <button
              onClick={() => navigateTo("/command-center")}
              className="inline-flex items-center gap-3 px-7 py-3.5 text-sm font-semibold tracking-wide text-white transition-all"
              style={{
                background: "linear-gradient(135deg, #3B82F6, #2563EB)",
                boxShadow: "0 0 24px rgba(59,130,246,0.4), 0 0 0 1px rgba(59,130,246,0.2)",
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.boxShadow =
                  "0 0 36px rgba(59,130,246,0.6), 0 0 0 1px rgba(59,130,246,0.3)")
              }
              onMouseLeave={(e) =>
                (e.currentTarget.style.boxShadow =
                  "0 0 24px rgba(59,130,246,0.4), 0 0 0 1px rgba(59,130,246,0.2)")
              }
            >
              Enter Command Center
              <span className="text-base">→</span>
            </button>
          </div>

          {/* Right — 3D map */}
          <div
            className="flex-1 relative rounded-sm overflow-hidden border border-slate-700/40 shadow-2xl flex flex-col -mt-12"
            style={{ height: "72vh", minHeight: 400 }}
          >
            <div className="flex items-center gap-1.5 px-4 py-3 border-b border-slate-700/40 bg-[#0a0c14] shrink-0 z-10">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
              <span className="ml-3 text-[11px] font-mono text-slate-500">
                triage.live
              </span>
            </div>
            <div className="flex-1 relative">
              <MapPreview />
              <div className="absolute inset-y-0 left-0 w-16 bg-gradient-to-r from-[#0a0a0f] to-transparent pointer-events-none" />
              <div className="absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-[#0a0a0f]/40 to-transparent pointer-events-none" />
              <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-[#0a0a0f] to-transparent pointer-events-none" />
              <div className="absolute bottom-4 left-4 text-[10px] font-mono text-slate-500 tracking-widest">
                NEW YORK CITY · LIVE TRIAGE VIEW
              </div>
            </div>
          </div>
        </div>

        {/* ── Right-side launch arrow ── */}
        <button
          onClick={() => navigateTo("/command-center")}
          className="absolute right-6 top-1/2 -translate-y-1/2 flex flex-col items-center gap-3 group"
          aria-label="Launch app"
        >
          <span
            className="text-[9px] font-mono tracking-[0.25em] text-slate-600 group-hover:text-blue-400 transition-colors"
            style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
          >
            LAUNCH
          </span>
          <span
            className="w-px flex-1 bg-slate-800 group-hover:bg-blue-500/40 transition-colors"
            style={{ height: 48 }}
          />
          {/* Animated chevrons */}
          <span className="flex flex-col items-center gap-0.5">
            {[0, 1, 2].map((i) => (
              <svg
                key={i}
                width="12"
                height="8"
                viewBox="0 0 12 8"
                fill="none"
                className="text-slate-600 group-hover:text-blue-400 transition-colors"
                style={{
                  animation: `arrowFade 1.4s ease-in-out ${i * 0.18}s infinite`,
                  opacity: 0,
                }}
              >
                <path
                  d="M1 1l5 5 5-5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ))}
          </span>
          <style>{`
            @keyframes arrowFade {
              0%,100%{opacity:0;transform:translateY(-4px)}
              50%{opacity:1;transform:translateY(0)}
            }
          `}</style>
        </button>
      </div>

      {/* ── Section 1: What we do ── */}
      <section className="border-t border-slate-800/60 px-12 lg:px-20 py-24">
        <div className="max-w-5xl mx-auto flex flex-col lg:flex-row gap-16">
          <div className="lg:w-56 shrink-0">
            <h2
              className="text-2xl font-semibold text-slate-200 mb-3"
              style={{ fontFamily: "var(--font-playfair)" }}
            >
              What we do
            </h2>
            <p className="text-slate-500 text-sm leading-relaxed">
              Three steps from disaster to decision.
            </p>
          </div>

          <div className="flex-1 relative">
            <div className="absolute left-[7px] top-2 bottom-2 w-px bg-slate-800" />
            <div className="flex flex-col gap-10">
              {STEPS.map((s, i) => (
                <div key={i} className="flex gap-6">
                  <div className="relative shrink-0 mt-1">
                    <span
                      className="w-3.5 h-3.5 rounded-full block"
                      style={
                        i === 0
                          ? {
                              background: "#f97316",
                              boxShadow: "0 0 10px rgba(249,115,22,0.6)",
                            }
                          : {
                              background: "#1e293b",
                              border: "1px solid #334155",
                            }
                      }
                    />
                  </div>
                  <div>
                    <div className="flex items-center gap-3 mb-1.5">
                      <span
                        className="text-[11px] font-mono"
                        style={{ color: i === 0 ? "#f97316" : "#475569" }}
                      >
                        {s.label}
                      </span>
                      <h3 className="text-base font-semibold text-slate-200">
                        {s.title}
                      </h3>
                    </div>
                    <p className="text-slate-400 text-sm leading-relaxed max-w-lg">
                      {s.body}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── Section 2: Capabilities grid ── */}
      <section className="border-t border-slate-800/60 px-6 lg:px-12 py-24 pb-40">
        <div className="max-w-7xl mx-auto">
          <h2
            className="text-[clamp(2.4rem,4.5vw,4rem)] font-semibold text-slate-200 leading-tight mb-3"
            style={{ fontFamily: "var(--font-space)" }}
          >
            Built for the worst moments.
          </h2>
          <div
            className="w-16 h-px mb-14"
            style={{ background: "linear-gradient(90deg,#f97316,transparent)" }}
          />

          <div className="grid grid-cols-1 md:grid-cols-3 border border-slate-800/60 divide-y md:divide-y-0 md:divide-x divide-slate-800/60">
            {CAPABILITIES.map((c, i) => (
              <div
                key={i}
                className="p-10 flex flex-col gap-5 bg-[#0a0c14] hover:bg-[#0d0f1a] transition-colors"
              >
                <span className="text-slate-500">{c.icon}</span>
                <div>
                  <h3
                    className="text-lg font-semibold text-slate-200 mb-3"
                    style={{ fontFamily: "var(--font-space)" }}
                  >
                    {c.title}
                  </h3>
                  <p
                    className="text-slate-400 text-[0.95rem] leading-relaxed"
                    style={{ fontFamily: "var(--font-space)" }}
                  >
                    {c.body}
                  </p>
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 border-x border-b border-slate-800/60 divide-y md:divide-y-0 md:divide-x divide-slate-800/60">
            <button
              onClick={() => navigateTo("/command-center")}
              className="flex items-center justify-between px-10 py-7 bg-[#0a0c14] hover:bg-[#0d0f1a] transition-colors group text-left"
            >
              <div>
                <div
                  className="text-base font-semibold text-slate-200 mb-1"
                  style={{ fontFamily: "var(--font-space)" }}
                >
                  Launch Command Center
                </div>
                <div
                  className="text-sm text-slate-500"
                  style={{ fontFamily: "var(--font-space)" }}
                >
                  Deploy scouts. Triage in real time.
                </div>
              </div>
              <span className="text-slate-500 group-hover:text-blue-400 transition-colors text-lg">
                →
              </span>
            </button>
            <div className="flex items-center justify-between px-10 py-7 bg-[#0a0c14]">
              <div>
                <div
                  className="text-base font-semibold text-slate-200 mb-1"
                  style={{ fontFamily: "var(--font-space)" }}
                >
                  Cross-reference intelligence
                </div>
                <div
                  className="text-sm text-slate-500"
                  style={{ fontFamily: "var(--font-space)" }}
                >
                  Scouts share findings. Hazards propagate automatically.
                </div>
              </div>
              <span className="text-slate-600 text-lg">→</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
