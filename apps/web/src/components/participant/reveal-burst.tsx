'use client'

import { useEffect, useEffectEvent, useRef } from 'react'

/*
 * The moment a card is revealed: gold coins, confetti and sparkles burst from the card and
 * fall away in about a second and a half. One canvas over the whole screen, drawn only while
 * the burst lasts. Skipped for people who prefer reduced motion. Decorative only.
 */

type Piece =
  | { kind: 'coin'; x: number; y: number; vx: number; vy: number; r: number; spin: number; phase: number }
  | { kind: 'flake'; x: number; y: number; vx: number; vy: number; w: number; h: number; rot: number; vr: number; colour: string }
  | { kind: 'spark'; x: number; y: number; size: number; delay: number }

const GOLD = ['#fff1b5', '#f6d26b', '#f4cb52', '#dba62d', '#bb841b', '#ffffff']
const DURATION = 1600

function star(ctx: CanvasRenderingContext2D, x: number, y: number, s: number) {
  ctx.beginPath()
  ctx.moveTo(x, y - s)
  ctx.lineTo(x + s * 0.18, y - s * 0.18)
  ctx.lineTo(x + s, y)
  ctx.lineTo(x + s * 0.18, y + s * 0.18)
  ctx.lineTo(x, y + s)
  ctx.lineTo(x - s * 0.18, y + s * 0.18)
  ctx.lineTo(x - s, y)
  ctx.lineTo(x - s * 0.18, y - s * 0.18)
  ctx.closePath()
  ctx.fill()
}

function coin(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, phase: number) {
  // A coin turning in the air: its width follows the cosine of its turn; the edge shows as
  // a darker rim when it is nearly edge-on.
  const squash = Math.max(0.12, Math.abs(Math.cos(phase)))
  ctx.save()
  ctx.translate(x, y)
  ctx.rotate(phase * 0.35)
  ctx.scale(squash, 1)
  const face = ctx.createLinearGradient(-r, -r, r, r)
  face.addColorStop(0, '#fff0a8')
  face.addColorStop(0.3, '#ffd447')
  face.addColorStop(0.55, '#d39318')
  face.addColorStop(0.8, '#f8cd45')
  face.addColorStop(1, '#ad6c08')
  ctx.fillStyle = '#8a5a09'
  ctx.beginPath()
  ctx.arc(0, 0, r, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = face
  ctx.beginPath()
  ctx.arc(0, 0, r * 0.86, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = 'rgb(255 242 191 / 0.8)'
  ctx.lineWidth = Math.max(1, r * 0.08)
  ctx.beginPath()
  ctx.arc(0, 0, r * 0.66, 0, Math.PI * 2)
  ctx.stroke()
  ctx.fillStyle = 'rgb(255 255 255 / 0.6)'
  ctx.beginPath()
  ctx.ellipse(-r * 0.32, -r * 0.42, r * 0.34, r * 0.13, -0.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

/** Fires once per new `fireKey`, from the centre of `anchor` (the big card). */
export function RevealBurst({ fireKey, anchor }: { fireKey: number; anchor: React.RefObject<HTMLElement | null> }) {
  const ref = useRef<HTMLCanvasElement>(null)

  const origin = useEffectEvent(() => {
    const box = anchor.current?.getBoundingClientRect()
    return box
      ? { x: box.left + box.width / 2, y: box.top + box.height / 2, size: box.width }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2, size: Math.min(window.innerWidth, window.innerHeight) * 0.5 }
  })

  useEffect(() => {
    if (!fireKey) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const width = window.innerWidth
    const height = window.innerHeight
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const { x: ox, y: oy, size } = origin()
    // Sizes and speeds follow the card, so the burst looks the same on a phone and a projector.
    const scale = Math.max(0.6, size / 420)
    const pieces: Piece[] = []
    for (let i = 0; i < 22; i += 1) {
      const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.5
      const speed = (7 + Math.random() * 9) * scale
      pieces.push({
        kind: 'coin',
        x: ox + (Math.random() - 0.5) * size * 0.3,
        y: oy + (Math.random() - 0.5) * size * 0.2,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 2 * scale,
        r: (9 + Math.random() * 9) * scale,
        spin: 0.12 + Math.random() * 0.2,
        phase: Math.random() * Math.PI,
      })
    }
    for (let i = 0; i < 70; i += 1) {
      const angle = Math.random() * Math.PI * 2
      const speed = (4 + Math.random() * 10) * scale
      pieces.push({
        kind: 'flake',
        x: ox,
        y: oy,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 4 * scale,
        w: (4 + Math.random() * 5) * scale,
        h: (7 + Math.random() * 7) * scale,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.35,
        colour: GOLD[Math.floor(Math.random() * GOLD.length)]!,
      })
    }
    for (let i = 0; i < 16; i += 1) {
      const angle = (i / 16) * Math.PI * 2 + Math.random() * 0.3
      const distance = size * (0.45 + Math.random() * 0.4)
      pieces.push({
        kind: 'spark',
        x: ox + Math.cos(angle) * distance,
        y: oy + Math.sin(angle) * distance * 0.9,
        size: (10 + Math.random() * 16) * scale,
        delay: Math.random() * 450,
      })
    }

    const gravity = 0.32 * scale
    const start = performance.now()
    let frame = 0
    let previous = start
    const draw = (now: number) => {
      const t = now - start
      // Steps in 60 fps frames, so a 120 Hz screen does not double the speed.
      const step = Math.min(3, (now - previous) / (1000 / 60))
      previous = now
      ctx.clearRect(0, 0, width, height)
      const fade = t > DURATION - 400 ? Math.max(0, (DURATION - t) / 400) : 1
      for (const p of pieces) {
        if (p.kind === 'spark') {
          const local = (t - p.delay) / 700
          if (local <= 0 || local >= 1) continue
          const pulse = Math.sin(local * Math.PI)
          ctx.globalAlpha = pulse
          const halo = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 0.9)
          halo.addColorStop(0, 'rgb(255 236 170 / 0.75)')
          halo.addColorStop(1, 'rgb(255 236 170 / 0)')
          ctx.fillStyle = halo
          ctx.beginPath()
          ctx.arc(p.x, p.y, p.size * 0.9, 0, Math.PI * 2)
          ctx.fill()
          ctx.fillStyle = '#fffbe8'
          star(ctx, p.x, p.y, p.size * (0.5 + pulse * 0.5))
          continue
        }
        p.vy += gravity * step
        p.vx *= Math.pow(0.985, step)
        p.x += p.vx * step
        p.y += p.vy * step
        ctx.globalAlpha = fade
        if (p.kind === 'coin') {
          p.phase += p.spin * step
          coin(ctx, p.x, p.y, p.r, p.phase)
        } else {
          p.rot += p.vr * step
          ctx.save()
          ctx.translate(p.x, p.y)
          ctx.rotate(p.rot)
          ctx.scale(1, Math.cos(p.rot * 1.7))
          ctx.fillStyle = p.colour
          ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h)
          ctx.restore()
        }
      }
      ctx.globalAlpha = 1
      if (t < DURATION) frame = requestAnimationFrame(draw)
      else ctx.clearRect(0, 0, width, height)
    }
    frame = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(frame)
      ctx.clearRect(0, 0, width, height)
    }
  }, [fireKey])

  return <canvas ref={ref} aria-hidden className="reveal-burst" />
}
