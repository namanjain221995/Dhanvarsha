/**
 * Dhanvarsha, "rain of wealth": gold coins falling behind the page in the gold look.
 *
 * Each coin is a real 3D object: a front face, a back face and a ridged edge between them
 * (about 12% of the diameter, like a ten-rupee coin), so a coin turning edge-on shows its
 * thickness instead of vanishing into a line. Coins fall with weight (they speed up as they
 * drop, sway little and tumble slowly), using CSS transforms only so phones stay smooth.
 *
 * Decorative only: hidden from assistive technology, never clickable, behind every card.
 * With reduced motion, three still coins rest at the top instead.
 */

type Coin = { x: number; size: number; fall: number; delay: number; sway: number; drift: number; spin: number; tilt: number; alpha: number }

// A fixed seed: the same coins on the server and in the browser (no hydration mismatch).
function makeCoins(count: number): Coin[] {
  let seed = 20261021
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
  return Array.from({ length: count }, (_, i) => {
    const depth = rand() // 0 = far, 1 = near
    return {
      x: ((i + rand() * 0.8) / count) * 100,
      size: Math.round(22 + depth * 28),
      fall: 8.5 - depth * 3 + rand() * 1.5, // near coins fall faster
      delay: -rand() * 10,
      sway: 1.8 + rand() * 1.4,
      drift: 3 + rand() * 9,
      spin: 2.4 + rand() * 1.8,
      tilt: 14 + rand() * 22,
      // Faint enough that text passing over a coin stays easy to read.
      alpha: 0.45 + depth * 0.45,
    }
  })
}

const COINS = makeCoins(14)
const EDGE_SLICES = 7

/** One 3D coin. The parent sets its size (and, for still coins, its pose). */
function CoinBody({ className = '', style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <span className={`coin-body ${className}`} style={style}>
      <svg className="coin-side coin-front" viewBox="0 0 64 64">
        <use href="#sv-coin" />
      </svg>
      {Array.from({ length: EDGE_SLICES }, (_, i) => (
        <span key={i} className="coin-edge" style={{ '--i': i / (EDGE_SLICES - 1) } as React.CSSProperties} />
      ))}
      <svg className="coin-side coin-back" viewBox="0 0 64 64">
        <use href="#sv-coin" />
      </svg>
    </span>
  )
}

export function CoinRain() {
  return (
    <div aria-hidden className="coin-rain">
      <svg width="0" height="0" className="absolute" focusable="false">
        <defs>
          <radialGradient id="sv-coin-face" cx="34%" cy="28%" r="78%">
            <stop offset="0" stopColor="#fff6cc" />
            <stop offset="0.3" stopColor="#f8d76a" />
            <stop offset="0.66" stopColor="#e2aa2e" />
            <stop offset="1" stopColor="#ad7412" />
          </radialGradient>
          <linearGradient id="sv-coin-rim" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fde7a0" />
            <stop offset="0.45" stopColor="#d49b25" />
            <stop offset="1" stopColor="#7f540a" />
          </linearGradient>
          <symbol id="sv-coin" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="32" fill="url(#sv-coin-rim)" />
            {/* Milled rim and the raised border around the face. */}
            <circle cx="32" cy="32" r="30.4" fill="none" stroke="#8a5d0c" strokeWidth="2.4" strokeDasharray="1.1 1.6" opacity="0.5" />
            <circle cx="32" cy="32" r="26" fill="#9a6810" />
            <circle cx="32" cy="32" r="25" fill="url(#sv-coin-face)" />
            <circle cx="32" cy="32" r="21.5" fill="none" stroke="#fff2bf" strokeWidth="0.9" opacity="0.7" />
            {/* Raised rupee: a light edge above, the struck colour below. */}
            <text x="32.9" y="43" textAnchor="middle" fontSize="28" fontWeight="800" fill="#fff4c8" opacity="0.7" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <text x="32" y="42.1" textAnchor="middle" fontSize="28" fontWeight="800" fill="#9c6a0d" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <ellipse cx="22" cy="16" rx="11" ry="4.6" fill="#ffffff" opacity="0.4" transform="rotate(-30 22 16)" />
          </symbol>
        </defs>
      </svg>
      {COINS.map((coin, index) => (
        <span
          key={index}
          className="coin"
          style={
            {
              '--x': `${coin.x}%`,
              '--size': `${coin.size}px`,
              '--fall': `${coin.fall}s`,
              '--delay': `${coin.delay}s`,
              '--sway': `${coin.sway}s`,
              '--drift': `${coin.drift}px`,
              '--spin': `${coin.spin}s`,
              '--tilt': `${coin.tilt}deg`,
              '--alpha': coin.alpha,
            } as React.CSSProperties
          }
        >
          <span className="coin-sway">
            <CoinBody className="coin-tumble" />
          </span>
        </span>
      ))}
      <span className="coin-still">
        <CoinBody style={{ '--size': '58px', width: 58, height: 58, right: 16, top: 8, transform: 'rotateX(52deg) rotateZ(-24deg)' } as React.CSSProperties} />
        <CoinBody style={{ '--size': '38px', width: 38, height: 38, right: 74, top: 6, transform: 'rotateX(38deg) rotateY(-28deg)' } as React.CSSProperties} />
        <CoinBody style={{ '--size': '30px', width: 30, height: 30, right: 8, top: 70, transform: 'rotateX(60deg) rotateZ(40deg)' } as React.CSSProperties} />
      </span>
    </div>
  )
}
