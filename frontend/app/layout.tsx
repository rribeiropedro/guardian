import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import './globals.css'
import MapboxTileCache from './_components/MapboxTileCache'

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] })
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'AEGIS-NET — AI Earthquake Incident Command',
  description: 'AI scout agents for earthquake incident command. Deploy scouts, triage buildings, walk the route.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full`}>
      <body className="h-full overflow-hidden bg-[#0a0a0f] text-slate-200 antialiased">
        <MapboxTileCache />
        {children}
      </body>
    </html>
  )
}
