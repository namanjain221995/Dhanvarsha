/**
 * Golden starlight on the gold look's dark blue pages: four-point sparkles that grow, flare and
 * fade at their own pace, and fine gold dust that twinkles in three groups.
 *
 * Built for phones: every sparkle is painted once and then animated with transform and opacity
 * only (no repaints). The dust is drawn with box-shadows on three elements, so 36 specks cost
 * three animations, not 36. Decorative only: hidden from assistive technology, never clickable,
 * behind every card. With reduced motion the stars rest, softly lit.
 */

type Sparkle = { x: number; y: number; size: number; cycle: number; delay: number }

// A fixed seed: the same sky on the server and in the browser (no hydration mismatch).
function makeSky(count: number, seed: number) {
  let s = seed
  const rand = () => {
    s = (s * 1664525 + 1013904223) % 4294967296
    return s / 4294967296
  }
  const sparkles: Sparkle[] = Array.from({ length: count }, (_, i) => ({
    // Spread over the screen, with a little randomness so it never looks like a grid.
    x: ((i % 4) + 0.15 + rand() * 0.7) * 25,
    y: ((Math.floor(i / 4) + 0.1 + rand() * 0.8) / Math.ceil(count / 4)) * 100,
    size: Math.round(24 + rand() * 26),
    cycle: 3.2 + rand() * 3.6,
    delay: -rand() * 7,
  }))
  const dust = [0, 1, 2].map(() =>
    Array.from({ length: 12 }, () => {
      const r = rand() < 0.3 ? 1.5 : 1
      return `${(rand() * 100).toFixed(1)}vw ${(rand() * 100).toFixed(1)}vh 0 ${r}px rgb(255 222 140 / ${(0.55 + rand() * 0.4).toFixed(2)})`
    }).join(', '),
  )
  return { sparkles, dust }
}

const SKY = makeSky(16, 20261022)

export function StarField() {
  return (
    <div aria-hidden className="star-field">
      {SKY.dust.map((shadow, i) => (
        <span key={`d${i}`} className="star-dust" style={{ boxShadow: shadow, animationDelay: `${-i * 1.3}s` }} />
      ))}
      {SKY.sparkles.map((s, i) => (
        <span
          key={i}
          className="star-sparkle"
          style={
            {
              left: `${s.x}%`,
              top: `${s.y}%`,
              '--size': `${s.size}px`,
              '--cycle': `${s.cycle}s`,
              '--delay': `${s.delay}s`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  )
}
