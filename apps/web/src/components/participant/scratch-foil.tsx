'use client'

import { useEffect, useEffectEvent, useLayoutEffect, useRef } from 'react'

/*
 * The scratch layer over the big card's face: a canvas painted as gold foil with fine glitter,
 * "SCRATCH TO REVEAL" and a coin that wiggles to show what to do. The presenter scratches it
 * away with a mouse or a finger (pointer events; touch-action: none so the page never scrolls
 * instead). Strokes are smooth curves through every pointer sample, so a fast swipe leaves a
 * continuous track rather than a row of dots.
 *
 * Every ~120 ms while scratching, the alpha is sampled on a coarse grid; once about 45% of the
 * foil is gone, `onComplete` fires once and the parent clears the rest. `onStart` fires on the
 * first touch, before any of the face shows through.
 */

/** The share of the foil that, once scratched away, reveals the whole card. */
const THRESHOLD = 0.45
const CHECK_MS = 120
/** Brush radius as a share of the card's width. */
const BRUSH = 0.07
/** Sampling grid for the progress check (GRID × GRID points). */
const GRID = 44

type Point = { x: number; y: number }

function cinzel(canvas: HTMLCanvasElement): string {
  // The canvas carries font-family: var(--font-cinzel) in CSS; its computed value names the
  // self-hosted face the rest of the page uses.
  return getComputedStyle(canvas).fontFamily || "'Times New Roman', serif"
}

/** Gold foil, glitter, a double frame and the instructions, at the canvas's own resolution. */
function paintFoil(canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D) {
  const w = canvas.width
  const h = canvas.height
  const unit = w / 100
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.clearRect(0, 0, w, h)

  // Polished metal: bright and dark bands across the diagonal, like the coins of the rain.
  const base = ctx.createLinearGradient(0, 0, w, h)
  base.addColorStop(0, '#b47c18')
  base.addColorStop(0.16, '#eebf4c')
  base.addColorStop(0.3, '#fff0b0')
  base.addColorStop(0.44, '#dcaa3e')
  base.addColorStop(0.58, '#b98019')
  base.addColorStop(0.72, '#f5d06a')
  base.addColorStop(0.86, '#fff3c4')
  base.addColorStop(1, '#c48b22')
  ctx.fillStyle = base
  ctx.fillRect(0, 0, w, h)

  // Light from above, shade below.
  const light = ctx.createRadialGradient(w * 0.28, h * 0.08, 0, w * 0.28, h * 0.08, Math.max(w, h) * 0.75)
  light.addColorStop(0, 'rgb(255 252 235 / 0.55)')
  light.addColorStop(1, 'rgb(255 252 235 / 0)')
  ctx.fillStyle = light
  ctx.fillRect(0, 0, w, h)
  const shade = ctx.createRadialGradient(w * 0.8, h * 1.05, 0, w * 0.8, h * 1.05, Math.max(w, h) * 0.8)
  shade.addColorStop(0, 'rgb(110 66 6 / 0.45)')
  shade.addColorStop(1, 'rgb(110 66 6 / 0)')
  ctx.fillStyle = shade
  ctx.fillRect(0, 0, w, h)

  // Brushed metal: faint diagonal hairlines.
  ctx.lineWidth = Math.max(1, unit * 0.12)
  for (let i = -h; i < w; i += unit * 0.9) {
    ctx.strokeStyle = Math.random() < 0.5 ? 'rgb(255 255 255 / 0.07)' : 'rgb(120 76 8 / 0.06)'
    ctx.beginPath()
    ctx.moveTo(i, h)
    ctx.lineTo(i + h * 0.55, 0)
    ctx.stroke()
  }

  // Glitter: fine specks of light and dark gold, and a few brighter glints.
  const specks = Math.round((w * h) / (unit * unit * 1.5))
  const colours = [
    'rgb(255 251 230 / 0.95)',
    'rgb(255 236 170 / 0.85)',
    'rgb(150 100 18 / 0.55)',
    'rgb(255 255 255 / 0.9)',
    'rgb(122 78 10 / 0.45)',
  ]
  for (let i = 0; i < specks; i += 1) {
    const r = unit * (0.08 + Math.random() * 0.22)
    ctx.fillStyle = colours[i % colours.length]!
    ctx.beginPath()
    ctx.arc(Math.random() * w, Math.random() * h, r, 0, Math.PI * 2)
    ctx.fill()
  }
  for (let i = 0; i < 26; i += 1) {
    const x = Math.random() * w
    const y = Math.random() * h
    const s = unit * (0.8 + Math.random() * 1.4)
    const glow = ctx.createRadialGradient(x, y, 0, x, y, s)
    glow.addColorStop(0, 'rgb(255 255 255 / 0.95)')
    glow.addColorStop(0.25, 'rgb(255 245 200 / 0.55)')
    glow.addColorStop(1, 'rgb(255 245 200 / 0)')
    ctx.fillStyle = glow
    ctx.beginPath()
    ctx.moveTo(x, y - s)
    ctx.lineTo(x + s * 0.16, y - s * 0.16)
    ctx.lineTo(x + s, y)
    ctx.lineTo(x + s * 0.16, y + s * 0.16)
    ctx.lineTo(x, y + s)
    ctx.lineTo(x - s * 0.16, y + s * 0.16)
    ctx.lineTo(x - s, y)
    ctx.lineTo(x - s * 0.16, y - s * 0.16)
    ctx.closePath()
    ctx.fill()
  }

  // A double frame, like a lottery ticket.
  const frame = (inset: number, width: number, colour: string, radius: number) => {
    ctx.lineWidth = width
    ctx.strokeStyle = colour
    ctx.beginPath()
    ctx.roundRect(inset, inset, w - inset * 2, h - inset * 2, radius)
    ctx.stroke()
  }
  frame(unit * 4.5, Math.max(1, unit * 0.45), 'rgb(104 64 6 / 0.55)', unit * 3.5)
  frame(unit * 6, Math.max(1, unit * 0.3), 'rgb(255 246 214 / 0.7)', unit * 2.6)

  // The instructions, struck into the metal: a light edge below, navy on top.
  const family = cinzel(canvas)
  const cx = w / 2
  const cy = h * 0.42
  const engrave = (text: string, y: number, size: number, spacing: number) => {
    ctx.font = `700 ${size}px ${family}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    if ('letterSpacing' in ctx) ctx.letterSpacing = `${spacing * size}px`
    // Letter spacing also follows the last letter; half a space each side keeps it centred.
    const shift = spacing * size * 0.5
    ctx.fillStyle = 'rgb(255 248 222 / 0.75)'
    ctx.fillText(text, cx + shift, y + Math.max(1, size * 0.045))
    ctx.fillStyle = 'rgb(27 31 77 / 0.88)'
    ctx.fillText(text, cx + shift, y)
  }
  const fit = (text: string, size: number, spacing: number, max: number) => {
    ctx.font = `700 ${size}px ${family}`
    if ('letterSpacing' in ctx) ctx.letterSpacing = `${spacing * size}px`
    const measured = ctx.measureText(text).width
    return measured > max ? (size * max) / measured : size
  }
  const big = fit('SCRATCH', unit * 13, 0.08, w * 0.78)
  const small = fit('TO REVEAL', unit * 7, 0.22, w * 0.72)
  engrave('SCRATCH', cy - big * 0.42, big, 0.08)
  engrave('TO REVEAL', cy + small * 0.95, small, 0.22)

  // A thin rule with a diamond under the words.
  const ruleY = cy + small * 2.1
  ctx.strokeStyle = 'rgb(27 31 77 / 0.45)'
  ctx.lineWidth = Math.max(1, unit * 0.3)
  ctx.beginPath()
  ctx.moveTo(cx - unit * 16, ruleY)
  ctx.lineTo(cx - unit * 2.4, ruleY)
  ctx.moveTo(cx + unit * 2.4, ruleY)
  ctx.lineTo(cx + unit * 16, ruleY)
  ctx.stroke()
  ctx.fillStyle = 'rgb(27 31 77 / 0.6)'
  ctx.beginPath()
  ctx.moveTo(cx, ruleY - unit * 1.3)
  ctx.lineTo(cx + unit * 1.3, ruleY)
  ctx.lineTo(cx, ruleY + unit * 1.3)
  ctx.lineTo(cx - unit * 1.3, ruleY)
  ctx.closePath()
  ctx.fill()

  if ('letterSpacing' in ctx) ctx.letterSpacing = '0px'
  ctx.restore()
}

export function ScratchFoil({
  width,
  height,
  onStart,
  onComplete,
}: {
  /** The card's size in CSS pixels when the foil is laid (the dialog may not be shown yet). */
  width: number
  height: number
  /** The first touch, before anything under the foil shows. */
  onStart: () => void
  /** About 45% of the foil is gone. Fires once. */
  onComplete: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const hintRef = useRef<HTMLSpanElement>(null)

  const started = useEffectEvent(() => onStart())
  const completed = useEffectEvent(() => onComplete())
  const paint = useEffectEvent((canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D) => paintFoil(canvas, ctx))
  const laidSize = useEffectEvent(() => ({ width, height }))

  // Size the canvas to the card (at most 2 device pixels per CSS pixel) and paint it before
  // the first frame shows. Its resolution stays fixed after that: CSS scales it with the card
  // (the pointer maths reads the drawn size), so scratches survive a window resize.
  useLayoutEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const size = laidSize()
    canvas.width = Math.max(1, Math.round(size.width * dpr))
    canvas.height = Math.max(1, Math.round(size.height * dpr))
    paint(canvas, ctx)

    let alive = true
    let touched = false
    let down: number | null = null
    let last: Point | null = null
    let lastMid: Point | null = null
    let lastCheck = 0
    let dirty = false
    let done = false
    const radius = () => canvas.width * BRUSH

    // In case Cinzel was still loading: paint again once it is ready, unless scratching began.
    // (A fallback face that names a font this computer lacks makes load() fail; that is fine.)
    const font = `700 20px ${cinzel(canvas)}`
    if (!document.fonts.check(font)) {
      document.fonts
        .load(font)
        .catch(() => [])
        .then(() => {
          if (alive && !touched) paint(canvas, ctx)
        })
    }

    const toCanvas = (event: PointerEvent): Point => {
      const box = canvas.getBoundingClientRect()
      return {
        x: ((event.clientX - box.left) / box.width) * canvas.width,
        y: ((event.clientY - box.top) / box.height) * canvas.height,
      }
    }

    const check = () => {
      if (done || !dirty) return
      dirty = false
      lastCheck = performance.now()
      const { width: w, height: h } = canvas
      const data = ctx.getImageData(0, 0, w, h).data
      let clear = 0
      for (let gy = 0; gy < GRID; gy += 1) {
        const y = Math.min(h - 1, Math.floor(((gy + 0.5) / GRID) * h))
        for (let gx = 0; gx < GRID; gx += 1) {
          const x = Math.min(w - 1, Math.floor(((gx + 0.5) / GRID) * w))
          if (data[(y * w + x) * 4 + 3]! < 128) clear += 1
        }
      }
      if (clear / (GRID * GRID) >= THRESHOLD) {
        done = true
        completed()
      }
    }

    const scratchTo = (point: Point) => {
      if (!last || !lastMid) return
      // A curve through the previous sample, from midpoint to midpoint: smooth at any speed.
      const mid = { x: (last.x + point.x) / 2, y: (last.y + point.y) / 2 }
      ctx.beginPath()
      ctx.moveTo(lastMid.x, lastMid.y)
      ctx.quadraticCurveTo(last.x, last.y, mid.x, mid.y)
      ctx.stroke()
      last = point
      lastMid = mid
      dirty = true
    }

    const onDown = (event: PointerEvent) => {
      if (done || down !== null || (event.pointerType === 'mouse' && event.button !== 0)) return
      event.preventDefault()
      down = event.pointerId
      try {
        canvas.setPointerCapture(event.pointerId)
      } catch {
        // Capture is a nicety: strokes that leave the card still end on pointerup.
      }
      if (!touched) {
        touched = true
        hintRef.current?.setAttribute('data-gone', '')
        started()
      }
      const point = toCanvas(event)
      ctx.globalCompositeOperation = 'destination-out'
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.lineWidth = radius() * 2
      ctx.beginPath()
      ctx.arc(point.x, point.y, radius(), 0, Math.PI * 2)
      ctx.fill()
      last = point
      lastMid = point
      dirty = true
    }

    const onMove = (event: PointerEvent) => {
      if (done || event.pointerId !== down) return
      const samples = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : []
      for (const sample of samples.length ? samples : [event]) scratchTo(toCanvas(sample))
      if (performance.now() - lastCheck >= CHECK_MS) check()
    }

    const onUp = (event: PointerEvent) => {
      if (event.pointerId !== down) return
      down = null
      if (last && lastMid && !done) {
        ctx.beginPath()
        ctx.moveTo(lastMid.x, lastMid.y)
        ctx.lineTo(last.x, last.y)
        ctx.stroke()
      }
      last = null
      lastMid = null
      check()
    }

    canvas.addEventListener('pointerdown', onDown)
    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerup', onUp)
    canvas.addEventListener('pointercancel', onUp)
    return () => {
      alive = false
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('pointercancel', onUp)
    }
  }, [])

  // A long press on a phone would otherwise open the image menu or select text.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const block = (event: Event) => event.preventDefault()
    canvas.addEventListener('contextmenu', block)
    return () => canvas.removeEventListener('contextmenu', block)
  }, [])

  return (
    <>
      <canvas ref={canvasRef} aria-hidden className="reveal-foil-canvas" />
      <span ref={hintRef} aria-hidden className="reveal-hint">
        <span className="reveal-hint-coin">
          <svg viewBox="0 0 64 64">
            <use href="#sv-coin" />
          </svg>
        </span>
      </span>
    </>
  )
}
