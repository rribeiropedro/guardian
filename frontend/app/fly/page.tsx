'use client'

import dynamic from 'next/dynamic'
import { useSearchParams } from 'next/navigation'

const FlyView = dynamic(() => import('./FlyView'), { ssr: false })

export default function FlyPage() {
  const searchParams = useSearchParams()
  const latParam = searchParams.get('lat')
  const lngParam = searchParams.get('lng')
  const nameParam = searchParams.get('name') || undefined

  const lat = latParam ? Number(latParam) : undefined
  const lng = lngParam ? Number(lngParam) : undefined

  const hasValidCoords =
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    lat! >= -90 &&
    lat! <= 90 &&
    lng! >= -180 &&
    lng! <= 180

  return (
    <FlyView
      initialLat={hasValidCoords ? lat : undefined}
      initialLng={hasValidCoords ? lng : undefined}
      locationName={nameParam}
    />
  )
}
