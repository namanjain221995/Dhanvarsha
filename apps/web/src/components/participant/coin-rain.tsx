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

type Coin = {
  x: number
  size: number
  fall: number
  delay: number
  sway: number
  drift: number
  spin: number
  tilt: number
  alpha: number
  twinkle: number
  twinkleDelay: number
}

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
      // Near coins solid and shining; far ones a little faint, which reads as distance.
      alpha: 0.72 + depth * 0.28,
      twinkle: 3.2 + rand() * 3.5,
      twinkleDelay: -rand() * 6,
    }
  })
}

const COINS = makeCoins(12)
const EDGE_SLICES = 6

/** One 3D coin. The parent sets its size (and, for still coins, its pose). */
function CoinBody({ className = '', style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <span className={`coin-body ${className}`} style={style}>
      {/* Each face: the struck coin, a glint of light that sweeps across it as it turns, and
          a shadow that deepens as it turns away from the light. */}
      <span className="coin-side coin-front">
        <svg viewBox="0 0 64 64">
          <use href="#sv-coin" />
        </svg>
        <span className="coin-glint" />
        <span className="coin-shade" />
        <span className="coin-spark" />
      </span>
      {Array.from({ length: EDGE_SLICES }, (_, i) => (
        <span key={i} className="coin-edge" style={{ '--i': i / (EDGE_SLICES - 1) } as React.CSSProperties} />
      ))}
      <span className="coin-side coin-back">
        <svg viewBox="0 0 64 64">
          <use href="#sv-coin" />
        </svg>
        <span className="coin-glint" />
        <span className="coin-shade" />
      </span>
    </span>
  )
}

export function CoinRain() {
  return (
    <div aria-hidden className="coin-rain">
      <svg width="0" height="0" className="absolute" focusable="false">
        <defs>
          {/* Polished metal: bright and dark bands, as gold reflects its surroundings. */}
          <linearGradient id="sv-coin-face" x1="0.12" y1="0" x2="0.88" y2="1">
            <stop offset="0" stopColor="#fff0a8" />
            <stop offset="0.14" stopColor="#ffd447" />
            <stop offset="0.33" stopColor="#d39318" />
            <stop offset="0.47" stopColor="#ffe27a" />
            <stop offset="0.58" stopColor="#f4b828" />
            <stop offset="0.79" stopColor="#ad6c08" />
            <stop offset="1" stopColor="#f8cd45" />
          </linearGradient>
          <radialGradient id="sv-coin-light" cx="30%" cy="24%" r="60%">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.7" />
            <stop offset="0.45" stopColor="#fff6d6" stopOpacity="0.18" />
            <stop offset="1" stopColor="#fff6d6" stopOpacity="0" />
          </radialGradient>
          {/* The rim runs the other way round, so face and rim read as a bevel. */}
          <linearGradient id="sv-coin-rim" x1="0.9" y1="1" x2="0.1" y2="0">
            <stop offset="0" stopColor="#fbe08c" />
            <stop offset="0.35" stopColor="#c58a1b" />
            <stop offset="0.6" stopColor="#fff0b6" />
            <stop offset="1" stopColor="#8a5a09" />
          </linearGradient>
          <linearGradient id="sv-coin-bevel" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fff7d1" />
            <stop offset="1" stopColor="#7d5208" />
          </linearGradient>
          <symbol id="sv-coin" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="32" fill="url(#sv-coin-rim)" />
            {/* Milled rim and the raised border around the face. */}
            <circle cx="32" cy="32" r="30.4" fill="none" stroke="#8a5d0c" strokeWidth="2.4" strokeDasharray="1.1 1.6" opacity="0.5" />
            <circle cx="32" cy="32" r="26" fill="none" stroke="url(#sv-coin-bevel)" strokeWidth="1.6" />
            <circle cx="32" cy="32" r="25" fill="url(#sv-coin-face)" />
            <circle cx="32" cy="32" r="25" fill="url(#sv-coin-light)" />
            <circle cx="32" cy="32" r="21.5" fill="none" stroke="#fff2bf" strokeWidth="0.9" opacity="0.75" />
            {/* Raised rupee: a light edge above, the struck colour below. */}
            <text x="32.9" y="43" textAnchor="middle" fontSize="28" fontWeight="800" fill="#fff4c8" opacity="0.7" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <text x="32" y="42.1" textAnchor="middle" fontSize="28" fontWeight="800" fill="#9c6a0d" fontFamily="system-ui, sans-serif">
              ₹
            </text>
            <ellipse cx="22" cy="16" rx="11" ry="4.2" fill="#ffffff" opacity="0.55" transform="rotate(-30 22 16)" />
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
              '--twinkle': `${coin.twinkle}s`,
              '--twinkle-delay': `${coin.twinkleDelay}s`,
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
