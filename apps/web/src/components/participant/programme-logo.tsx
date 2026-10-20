'use client'

import { useState } from 'react'
import { Avatar } from '@/components/ui/misc'

/**
 * The programme's own logo at its own shape, with no frame or tint behind it. Without a
 * logo (or if it fails to load) the initials badge stands in.
 */
export function ProgrammeLogo({ src, name, height }: { src: string | null | undefined; name: string; height: number }) {
  const [failed, setFailed] = useState<string | null>(null)
  if (!src || failed === src) return <Avatar name={name} size={height} rounded="xl" />
  return (
    // eslint-disable-next-line @next/next/no-img-element -- images are pre-optimised WebP from the API
    <img
      src={src}
      alt=""
      aria-hidden
      decoding="async"
      className="block w-auto shrink-0 object-contain"
      style={{ height, maxWidth: height * 3.5 }}
      onError={() => setFailed(src)}
    />
  )
}
