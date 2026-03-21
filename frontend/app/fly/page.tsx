'use client'

import dynamic from 'next/dynamic'

const FlyView = dynamic(() => import('./FlyView'), { ssr: false })

export default function FlyPage() {
  return <FlyView />
}
