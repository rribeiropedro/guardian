'use client'

import { useRef, useState } from 'react'

interface Result {
  place_name: string
  center: [number, number]  // [lng, lat]
}

interface Props {
  onSelect: (center: [number, number]) => void
}

export default function LocationSearch({ onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value
    setQuery(val)

    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!val.trim()) { setResults([]); return }

    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN
        const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(val)}.json?access_token=${token}&types=place,address,poi,region&limit=5`
        const res = await fetch(url)
        const data = await res.json()
        setResults(data.features ?? [])
      } finally {
        setLoading(false)
      }
    }, 300)
  }

  function handleSelect(result: Result) {
    setQuery(result.place_name)
    setResults([])
    onSelect(result.center)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && results.length > 0) {
      handleSelect(results[0])
    }
    if (e.key === 'Escape') {
      setResults([])
    }
  }

  return (
    <div className="relative w-64">
      <div className="flex items-center gap-2 px-3 py-2 rounded-xl border border-white/10 bg-[rgba(8,10,18,0.85)] backdrop-blur-md">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="text-slate-500 shrink-0">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Search location…"
          className="bg-transparent text-xs font-mono text-slate-200 placeholder-slate-600 focus:outline-none w-full"
        />
        {loading && (
          <span className="h-1.5 w-1.5 rounded-full bg-blue-400 arriving-pulse shrink-0" />
        )}
      </div>

      {results.length > 0 && (
        <div className="absolute top-full mt-1 left-0 right-0 rounded-xl border border-white/10 bg-[rgba(8,10,18,0.97)] backdrop-blur-md overflow-hidden z-50 shadow-2xl">
          {results.map((r, i) => (
            <button
              key={i}
              onClick={() => handleSelect(r)}
              className="w-full text-left px-3 py-2 text-xs font-mono text-slate-300 hover:bg-white/5 transition-colors border-b border-white/[0.04] last:border-0 truncate"
            >
              {r.place_name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
