'use client'

import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { initials } from '@/components/ui/misc'
import { CardBack, lengthTier } from './opening-card-face'
import { RevealBurst } from './reveal-burst'
import type { Box, Slot } from './reveal-stage'
import { paintFoil } from './scratch-foil'

/*
 * The spotlight's moment (spotlight-screen.tsx): the organiser presses "Show" in the console,
 * and on the big screen that person's card leaves its place, flies to the middle and grows
 * (a FLIP animation, transform only), a gold coin scratches its foil away by itself, and the
 * card pops with a burst of coins to show the picture and the name. After the hold time (or
 * when the organiser sends it back, or shows someone else) it flies back to its place, face up.
 *
 * Nobody touches the screen: it is not a dialog and takes no focus. A card shown again comes
 * out face up, without the foil.
 */

export type SpotlightPerson = { id: string; name: string; detail: string; image_url: string | null }

/** Portrait cards: width / height. */
export const SPOT_ASPECT = 3 / 4

const OPEN_MS = 720
const CLOSE_MS = 560
/** A breath on the foil before the coin starts. */
const SUSPENSE_MS = 450
const SCRATCH_MS = 2300
const POP = 'cubic-bezier(0.3, 1.32, 0.55, 1)'
const GLIDE = 'cubic-bezier(0.45, 0, 0.2, 1)'

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** The big card: as tall as the screen allows (82 % of it), within 88 % of its width. */
function bigSize(vw: number, vh: number) {
  const height = Math.max(160, Math.min(vh * 0.82, (vw * 0.88) / SPOT_ASPECT))
  return { width: Math.round(height * SPOT_ASPECT), height: Math.round(height) }
}

/** The transform that puts an element laid out at `to` exactly over `from`. */
function flip(from: Box, to: DOMRect): string {
  const dx = from.left + from.width / 2 - (to.left + to.width / 2)
  const dy = from.top + from.height / 2 - (to.top + to.height / 2)
  return `translate(${dx}px, ${dy}px) scale(${from.width / to.width}, ${from.height / to.height})`
}

/**
 * A revealed card's face: the picture in a gold frame, the name in gold capitals and the line
 * under it. Sized in the card's own units (cqw), so the grid card and the big one match.
 */
export function SpotlightFace({ person }: { person: SpotlightPerson }) {
  const [failed, setFailed] = useState<string | null>(null)
  const image = person.image_url && failed !== person.image_url ? person.image_url : null
  return (
    <span className="spot-face">
      <span className="spot-photo">
        {image ? (
          // eslint-disable-next-line @next/next/no-img-element -- re-encoded WebP from the API
          <img src={image} alt="" decoding="async" draggable={false} onError={() => setFailed(image)} />
        ) : (
          <span aria-hidden className="spot-initials">
            {initials(person.name)}
          </span>
        )}
      </span>
      <span className="spot-name" data-size={lengthTier(person.name, 16, 26)}>
        {person.name}
      </span>
      {person.detail ? <span className="spot-detail">{person.detail}</span> : null}
    </span>
  )
}

type Point = { x: number; y: number }

/**
 * The foil, scratched away by a coin that sweeps the card from top to bottom in a zigzag, the
 * way a hand would. `onDone` fires once the sweep is over; the parent then fades the rest.
 */
function AutoScratch({ width, height, start, onDone }: { width: number; height: number; start: boolean; onDone: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const coinRef = useRef<HTMLSpanElement>(null)
  const done = useEffectEvent(() => onDone())
  const laidSize = useEffectEvent(() => ({ width, height }))

  // Paint the foil before the first frame shows, at the card's size (CSS scales it after).
  useLayoutEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const size = laidSize()
    canvas.width = Math.max(1, Math.round(size.width * dpr))
    canvas.height = Math.max(1, Math.round(size.height * dpr))
    paintFoil(canvas, ctx)
    // Cinzel may still be loading: paint again once it is ready (nothing scratched yet).
    const font = `700 20px ${getComputedStyle(canvas).fontFamily}`
    if (!document.fonts.check(font)) {
      document.fonts
        .load(font)
        .catch(() => [])
        .then(() => {
          if (canvas.isConnected && !canvas.hasAttribute('data-started')) paintFoil(canvas, ctx)
        })
    }
  }, [])

  useEffect(() => {
    if (!start) return
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    canvas.setAttribute('data-started', '')
    const w = canvas.width
    const h = canvas.height
    const r = w * 0.13
    // The path: rows across the card, each a little lower at its far end, plus a last pass
    // along the middle so no island of foil is left.
    const points: Point[] = []
    const rows = Math.max(3, Math.ceil((h - r) / (r * 1.45)))
    for (let i = 0; i <= rows; i += 1) {
      const y = r * 0.55 + (i / rows) * (h - r * 1.1)
      const rightward = i % 2 === 0
      points.push({ x: rightward ? r * 0.35 : w - r * 0.35, y: y - r * 0.28 }, { x: rightward ? w - r * 0.35 : r * 0.35, y: y + r * 0.28 })
    }
    points.push({ x: w * 0.5, y: h - r * 0.6 }, { x: w * 0.5, y: r * 0.6 })
    const lengths = [0]
    for (let i = 1; i < points.length; i += 1) {
      const a = points[i - 1]!
      const b = points[i]!
      lengths.push(lengths[i - 1]! + Math.hypot(b.x - a.x, b.y - a.y))
    }
    const total = lengths[lengths.length - 1]!
    const at = (distance: number): Point => {
      let i = 1
      while (i < lengths.length - 1 && lengths[i]! < distance) i += 1
      const a = points[i - 1]!
      const b = points[i]!
      const span = lengths[i]! - lengths[i - 1]! || 1
      const t = Math.min(1, Math.max(0, (distance - lengths[i - 1]!) / span))
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    }

    ctx.globalCompositeOperation = 'destination-out'
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.lineWidth = r * 2
    const scale = canvas.getBoundingClientRect().width / w || 1
    const coin = coinRef.current
    if (coin) coin.style.opacity = '1'

    const still = reducedMotion()
    const duration = still ? 1 : SCRATCH_MS
    let drawn = 0
    let begun: number | null = null
    let frame = 0
    const step = (now: number) => {
      if (begun === null) begun = now
      const t = Math.min(1, (now - begun) / duration)
      // Slow in, quick in the middle, slow out: like a hand.
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      const target = eased * total
      const from = at(drawn)
      ctx.beginPath()
      ctx.moveTo(from.x, from.y)
      for (let i = 1; i < points.length; i += 1) {
        if (lengths[i]! > drawn && lengths[i]! < target) ctx.lineTo(points[i]!.x, points[i]!.y)
      }
      const to = at(target)
      ctx.lineTo(to.x, to.y)
      ctx.stroke()
      drawn = target
      if (coin) {
        const wobble = Math.sin(now / 70) * 14
        coin.style.transform = `translate(${to.x * scale}px, ${to.y * scale}px) translate(-50%, -50%) rotate(${-18 + wobble}deg)`
      }
      if (t < 1) {
        frame = requestAnimationFrame(step)
      } else {
        if (coin) coin.style.opacity = '0'
        done()
      }
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [start])

  return (
    <>
      <canvas ref={canvasRef} aria-hidden className="reveal-foil-canvas spot-foil-canvas" />
      <span ref={coinRef} aria-hidden className="spot-coin">
        <svg viewBox="0 0 64 64">
          <use href="#sv-coin" />
        </svg>
      </span>
    </>
  )
}

export function SpotlightStage({
  person,
  number,
  origin,
  first,
  holdMs,
  closeKey,
  onClosed,
}: {
  person: SpotlightPerson
  number: number
  /** The card's place in the grid when the show began. */
  origin: Slot
  /** Revealed by this show: it comes out face down and scratches itself open. */
  first: boolean
  /** How long it stays in the middle once revealed; 0: until `closeKey` changes. */
  holdMs: number
  /** Changes when the card must go back now (sent back, someone else shown). */
  closeKey: number
  onClosed: (id: string) => void
}) {
  const backdropRef = useRef<HTMLDivElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const backRef = useRef<HTMLSpanElement>(null)
  const foilRef = useRef<HTMLDivElement>(null)
  const auraRef = useRef<HTMLSpanElement>(null)
  const flightRef = useRef<Animation | null>(null)
  const closingRef = useRef(false)
  const revealedRef = useRef(!first)
  const holdRef = useRef(0)

  const [viewport, setViewport] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }))
  const [scratching, setScratching] = useState(false)
  // The face is in the page only from the moment the coin starts (or at once when shown again).
  const [armed, setArmed] = useState(!first)
  const [foilGone, setFoilGone] = useState(!first)
  const [revealed, setRevealed] = useState(!first)
  const [burst, setBurst] = useState(0)

  const size = bigSize(viewport.w, viewport.h)

  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const requestClose = () => {
    if (closingRef.current) return
    closingRef.current = true
    window.clearTimeout(holdRef.current)
    const cardEl = cardRef.current
    const backdrop = backdropRef.current
    if (!cardEl || !backdrop) {
      onClosed(person.id)
      return
    }
    const still = reducedMotion()
    // Sent back before the coin finished: the rest of the foil melts away on the way.
    const foil = foilRef.current
    if (foil) {
      const opacity = getComputedStyle(foil).opacity
      for (const animation of foil.getAnimations()) animation.cancel()
      foil.animate([{ opacity }, { opacity: 0 }], { duration: 180, easing: 'ease-out', fill: 'forwards' })
    }
    const back = backRef.current
    if (back) {
      for (const animation of back.getAnimations()) animation.cancel()
      back.style.visibility = 'hidden'
    }
    if (!armed) flushSync(() => setArmed(true))
    const from = getComputedStyle(cardEl).transform
    for (const animation of cardEl.getAnimations()) animation.cancel()
    flightRef.current = null
    const slot = document.querySelector<HTMLElement>(`[data-spot-slot="${CSS.escape(person.id)}"]`)
    const home = slot?.getBoundingClientRect()
    let away: Animation
    if (still || !home || home.width === 0) {
      away = cardEl.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 220, easing: 'ease-in', fill: 'forwards' })
    } else {
      const to = flip({ left: home.left, top: home.top, width: home.width, height: home.height }, cardEl.getBoundingClientRect())
      away = cardEl.animate([{ transform: from }, { transform: to }], { duration: CLOSE_MS, easing: GLIDE, fill: 'forwards' })
    }
    backdrop.animate([{ opacity: getComputedStyle(backdrop).opacity }, { opacity: 0 }], {
      duration: still ? 220 : CLOSE_MS,
      easing: 'ease-in',
      fill: 'forwards',
    })
    away.finished.then(
      () => onClosed(person.id),
      () => {},
    )
  }

  const hold = () => {
    window.clearTimeout(holdRef.current)
    if (holdMs > 0 && !closingRef.current) holdRef.current = window.setTimeout(requestClose, holdMs)
  }

  const celebrate = () => {
    const still = reducedMotion()
    if (still || closingRef.current) return
    cardRef.current?.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.06)', offset: 0.32 }, { transform: 'scale(1)' }], {
      duration: 600,
      easing: 'cubic-bezier(0.3, 0.7, 0.4, 1)',
    })
    auraRef.current?.animate(
      [
        { opacity: 0, transform: 'scale(0.75)' },
        { opacity: 1, transform: 'scale(1.02)', offset: 0.28 },
        { opacity: 0, transform: 'scale(1.3)' },
      ],
      { duration: 1400, easing: 'ease-out' },
    )
    setBurst((n) => n + 1)
  }

  /** The coin has swept the card: the rest of the foil fades, the card pops, the hold begins. */
  const reveal = () => {
    if (revealedRef.current || closingRef.current) return
    revealedRef.current = true
    setRevealed(true)
    const foil = foilRef.current
    if (foil) {
      foil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: reducedMotion() ? 200 : 520, easing: 'ease-out', fill: 'forwards' }).finished.then(
        () => setFoilGone(true),
        () => {},
      )
    } else {
      setFoilGone(true)
    }
    celebrate()
    hold()
  }

  const opened = useEffectEvent(() => ({ origin, first }))
  const landed = useEffectEvent((wasFirst: boolean) => {
    if (closingRef.current) return
    if (wasFirst) {
      window.setTimeout(() => {
        if (closingRef.current) return
        flushSync(() => setArmed(true))
        setScratching(true)
      }, reducedMotion() ? 0 : SUSPENSE_MS)
    } else {
      celebrate()
      hold()
    }
  })

  // Fly the card from its place in the grid to the middle.
  useLayoutEffect(() => {
    const cardEl = cardRef.current
    const backdrop = backdropRef.current
    if (!cardEl || !backdrop) return
    const { origin: from, first: wasFirst } = opened()
    const still = reducedMotion()
    const animations: Animation[] = []
    animations.push(backdrop.animate([{ opacity: 0 }, { opacity: 1 }], { duration: still ? 200 : 420, easing: 'ease-out', fill: 'backwards' }))
    let flight: Animation
    if (still) {
      flight = cardEl.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: 'ease-out', fill: 'backwards' })
    } else {
      flight = cardEl.animate([{ transform: flip(from, cardEl.getBoundingClientRect()) }, { transform: 'none' }], {
        duration: OPEN_MS,
        easing: POP,
        fill: 'backwards',
      })
    }
    flightRef.current = flight
    animations.push(flight)
    const back = backRef.current
    if (back && wasFirst) {
      // The back flies in, then fades into the foil underneath it.
      const fade = back.animate([{ opacity: 1 }, { opacity: 0 }], {
        duration: still ? 160 : 320,
        delay: still ? 0 : OPEN_MS - 120,
        easing: 'ease-in-out',
        fill: 'both',
      })
      fade.finished.then(
        () => {
          if (!closingRef.current) back.style.visibility = 'hidden'
        },
        () => {},
      )
      animations.push(fade)
    }
    flight.finished.then(
      () => landed(wasFirst),
      () => {},
    )
    return () => {
      for (const animation of animations) animation.cancel()
      flightRef.current = null
      window.clearTimeout(holdRef.current)
    }
  }, [])

  // Sent back, or someone else shown: go back now.
  const firstKey = useRef(closeKey)
  const close = useEffectEvent(() => requestClose())
  useEffect(() => {
    if (closeKey !== firstKey.current) close()
  }, [closeKey])

  return (
    <div className="spot-stage" data-spot-stage="">
      <div ref={backdropRef} className="reveal-backdrop" />
      <div
        className="reveal-stage"
        style={
          {
            width: size.width,
            height: size.height,
            '--big-radius': `${Math.round((origin.radius * size.width) / Math.max(1, origin.width))}px`,
          } as React.CSSProperties
        }
      >
        <span ref={auraRef} aria-hidden className="reveal-aura" />
        <div ref={cardRef} className="reveal-card spot-big" data-revealed={revealed ? '' : undefined}>
          {armed ? <SpotlightFace person={person} /> : null}
          {foilGone ? null : (
            <div ref={foilRef} className="reveal-foil">
              <AutoScratch width={size.width} height={size.height} start={scratching} onDone={reveal} />
            </div>
          )}
          {first ? (
            <span ref={backRef} className="reveal-flight">
              <CardBack number={number} />
            </span>
          ) : null}
        </div>
      </div>
      <RevealBurst fireKey={burst} anchor={cardRef} />
      <p role="status" className="sr-only">
        {revealed ? [person.name, person.detail].filter(Boolean).join(', ') : ''}
      </p>
    </div>
  )
}
