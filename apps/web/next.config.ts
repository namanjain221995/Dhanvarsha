import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  // Images are already re-encoded and size-bounded by the API; no runtime optimiser.
  images: { unoptimized: true },
}

export default nextConfig
