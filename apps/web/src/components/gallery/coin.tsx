import { User } from 'lucide-react'

/**
 * A face struck on a gold coin, the gallery's one flourish (Dhanvarsha means a shower of wealth, and
 * the event's look rains coins). The ridged rim is CSS; the picture is a round crop of the face.
 */
export function Coin({ src, alt = '', size, spin = false }: { src: string | null; alt?: string; size?: 'small' | 'large'; spin?: boolean }) {
  return (
    <span className="gallery-coin" data-size={size} data-spin={spin ? '' : undefined}>
      <span className="gallery-coin-face">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element -- a small WebP from the API
          <img src={src} alt={alt} loading="lazy" decoding="async" />
        ) : (
          <User aria-hidden strokeWidth={1.5} />
        )}
      </span>
    </span>
  )
}
