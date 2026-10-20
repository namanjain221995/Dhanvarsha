'use client'

import { useEffect, useRef } from 'react'

/**
 * A short, restrained burst (about one second) shown only after the server has
 * confirmed a vote. Skipped entirely for people who prefer reduced motion.
 */
export function Confetti({ fireKey }: { fireKey: string | null }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!fireKey) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const width = window.innerWidth
    const height = window.innerHeight
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)
    const themed = canvas.closest('[data-accent]')
    const primary = getComputedStyle(themed ?? document.documentElement).getPropertyValue('--primary').trim() || '#5b34c4'
    const colors =
      themed?.getAttribute('data-accent') === 'gold'
        ? ['#f4cb52', '#dba62d', '#ffe9a8', '#bb841b', primary]
        : [primary, '#b8892b', '#2b1631', '#e9d9a8', primary]
    const originX = width / 2
    const originY = Math.min(height * 0.38, 320)
    const pieces = Array.from({ length: 64 }, () => {
      const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.1
      const speed = 5 + Math.random() * 6
      return {
        x: originX,
        y: originY,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        w: 5 + Math.random() * 5,
        h: 8 + Math.random() * 6,
        r: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.3,
        color: colors[Math.floor(Math.random() * colors.length)] ?? primary,
      }
    })
    const start = performance.now()
    const duration = 1100
    let frame = 0
    const draw = (now: number) => {
      const t = now - start
      ctx.clearRect(0, 0, width, height)
      const fade = t > duration - 300 ? Math.max(0, (duration - t) / 300) : 1
      for (const p of pieces) {
        p.vy += 0.28
        p.vx *= 0.985
        p.x += p.vx
        p.y += p.vy
        p.r += p.vr
        ctx.save()
        ctx.globalAlpha = fade
        ctx.translate(p.x, p.y)
        ctx.rotate(p.r)
        ctx.fillStyle = p.color
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h)
        ctx.restore()
      }
      if (t < duration) frame = requestAnimationFrame(draw)
      else ctx.clearRect(0, 0, width, height)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [fireKey])

  return <canvas ref={ref} aria-hidden className="pointer-events-none fixed inset-0 z-50 size-full" />
}
