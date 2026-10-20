/**
 * Dhanvarsha, "rain of wealth": gold coins falling behind the page in the gold look.
 *
 * Decorative only: hidden from assistive technology, never clickable, and behind every card.
 * Each coin falls, sways and spins at its own pace (CSS transforms only, so phones stay
 * smooth). Smaller coins are fainter and slower, so they read as further away. With reduced
 * motion, three still coins rest at the top instead.
 */

type Coin = { x: number; size: number; fall: number; delay: number; sway: number; drift: number; spin: number; alpha: number }

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
      size: Math.round(16 + depth * 30),
      fall: 14 - depth * 6 + rand() * 2,
      delay: -rand() * 16,
      sway: 2.2 + rand() * 2.4,
      drift: 8 + rand() * 22,
      spin: 1.3 + rand() * 2.2,
      // Faint enough that text passing over a coin stays easy to read.
      alpha: 0.38 + depth * 0.47,
    }
  })
}

const COINS = makeCoins(16)

export function CoinRain() {
  return (
    <div aria-hidden className="coin-rain">
      <svg width="0" height="0" className="absolute" focusable="false">
        <defs>
          <radialGradient id="sv-coin-face" cx="34%" cy="28%" r="78%">
            <stop offset="0" stopColor="#fff6cc" />
            <stop offset="0.32" stopColor="#f8d76a" />
            <stop offset="0.68" stopColor="#e2aa2e" />
            <stop offset="1" stopColor="#b37a14" />
          </radialGradient>
          <linearGradient id="sv-coin-rim" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fde7a0" />
            <stop offset="0.45" stopColor="#d49b25" />
            <stop offset="1" stopColor="#7f540a" />
          </linearGradient>
          <symbol id="sv-coin" viewBox="0 0 64 64">
            <ellipse cx="33" cy="35" rx="29" ry="29" fill="#7a4f08" opacity="0.28" />
            <circle cx="32" cy="32" r="30" fill="url(#sv-coin-rim)" />
            {/* Milled edge. */}
            <circle cx="32" cy="32" r="28.6" fill="none" stroke="#8a5d0c" strokeWidth="2.2" strokeDasharray="1.1 1.7" opacity="0.55" />
            <circle cx="32" cy="32" r="24.5" fill="url(#sv-coin-face)" />
            <circle cx="32" cy="32" r="24.5" fill="none" stroke="#a6720f" strokeWidth="1.4" />
            <circle cx="32" cy="32" r="21" fill="none" stroke="#fff2bf" strokeWidth="0.9" opacity="0.7" />
            {/* Raised rupee: a light edge above, the face colour below. */}
            <text x="32.8" y="42.6" textAnchor="middle" fontSize="27" fontWeight="800" fill="#fff4c8" opacity="0.65" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <text x="32" y="41.8" textAnchor="middle" fontSize="27" fontWeight="800" fill="#9c6a0d" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <ellipse cx="22" cy="17" rx="10" ry="4.5" fill="#ffffff" opacity="0.42" transform="rotate(-28 22 17)" />
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
              '--alpha': coin.alpha,
            } as React.CSSProperties
          }
        >
          <span className="coin-sway">
            <svg className="coin-face" viewBox="0 0 64 64">
              <use href="#sv-coin" />
            </svg>
          </span>
        </span>
      ))}
      <span className="coin-still">
        <svg viewBox="0 0 64 64" style={{ width: 54, right: 18, top: 10, transform: 'rotate(-18deg)' }}>
          <use href="#sv-coin" />
        </svg>
        <svg viewBox="0 0 64 64" style={{ width: 34, right: 70, top: 4, transform: 'rotate(12deg)' }}>
          <use href="#sv-coin" />
        </svg>
        <svg viewBox="0 0 64 64" style={{ width: 26, right: 6, top: 64, transform: 'rotate(30deg)' }}>
          <use href="#sv-coin" />
        </svg>
      </span>
    </div>
  )
}
