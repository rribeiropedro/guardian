import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // mapbox-gl ships ESM that needs to be transpiled for the browser bundle
  transpilePackages: ['mapbox-gl'],
}

export default nextConfig
