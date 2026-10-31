'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, participantApi } from '@/lib/api'
import type { PublicSpotlight } from '@/lib/types'
import { CoinRain } from './coin-rain'
import { FullscreenToggle, useFullscreenStage, useIdleAttribute, useIsFullscreen } from './fullscreen'
import { CardBack, lengthTier } from './opening-card-face'
import type { Slot } from './reveal-stage'
import { SPOT_ASPECT, SpotlightFace, SpotlightStage, type SpotlightPerson } from './spotlight-stage'
import { BrandEmblem, StageArcs } from './stage-sign-in'
import { StarField } from './star-field'

/*
 * The spotlight screen (/spotlight): a public page for the big screen, in the opening page's
 * gold look. One face-down gold card per person the organiser entered in the console. When the
 * organiser presses "Show" for someone, that card flies to the middle, scratches itself open
 * and shows the picture and the name (spotlight-stage.tsx), then goes back to its place face
 * up. Nobody touches this screen: it follows the console.
 *
 * GET /public/spotlight is asked every second (one request at a time). Each Show carries a
 * number (seq) and plays once; a screen that opens later shows the cards as they are, without
 * replaying the last Show. The API names only revealed people, so the page never holds the next
 * name before its moment.
 */

const POLL_MS = 1000
/** Below this card width the page scrolls in two columns instead of fitting one screen. */
const MIN_FIT_WIDTH = 96

type Command = { id: string; seq: number; first: boolean }
type Active = Command & { person: SpotlightPerson; number: number; origin: Slot }

function useSpotlight() {
  const [data, setData] = useState<PublicSpotlight | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    let inflight: AbortController | null = null
    let last = ''
    const load = async () => {
      if (inflight) return
      const controller = new AbortController()
      inflight = controller
      try {
        const next = await participantApi<PublicSpotlight>('/public/spotlight', { timeoutMs: 8000, signal: controller.signal })
        if (!alive) return
        const json = JSON.stringify(next)
        if (json !== last) {
          last = json
          setData(next)
        }
        setError(null)
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'Could not reach the server.')
      } finally {
        if (inflight === controller) inflight = null
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), POLL_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      alive = false
      inflight?.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [attempt])

  return { data, error, retry: () => setAttempt((n) => n + 1) }
}

/** A picture is loaded before its card flies, so the face never pops in late (4 s at most). */
function preload(url: string | null): Promise<void> {
  if (!url) return Promise.resolve()
  return new Promise((resolve) => {
    const image = new Image()
    const timer = window.setTimeout(resolve, 4000)
    image.onload = image.onerror = () => {
      window.clearTimeout(timer)
      resolve()
    }
    image.src = url
  })
}

/**
 * The card width that fits every card on one screen under the header, or 0 when the screen is
 * too small for that (the page then scrolls).
 */
function fitWidth(count: number, width: number, height: number, head: number): number {
  if (count < 1 || width < 640) return 0
  const padX = Math.max(16, width * 0.025)
  const padY = Math.max(16, height * 0.03)
  const gap = Math.max(10, Math.min(width, height) * 0.018)
  const room = height - head - padY * 2 - gap * 1.5
  let best = 0
  for (let cols = 1; cols <= Math.min(count, 12); cols += 1) {
    const rows = Math.ceil(count / cols)
    const byWidth = (width - padX * 2 - (cols - 1) * gap) / cols
    const byHeight = ((room - (rows - 1) * gap) / rows) * SPOT_ASPECT
    best = Math.max(best, Math.min(byWidth, byHeight, width * 0.24))
  }
  return best >= MIN_FIT_WIDTH ? Math.floor(best) : 0
}

function Header({ data, headerRef }: { data: PublicSpotlight; headerRef: React.Ref<HTMLElement> }) {
  return (
    <header ref={headerRef} className="opening-header spot-header">
      <div className="stage-emblem-wrap opening-emblem">
        <BrandEmblem src={data.logo_url} />
      </div>
      <h1 className="opening-heading stage-rise" style={{ animationDelay: '0.2s' }}>
        <span className="stage-title opening-title" data-size={lengthTier(data.title, 14, 24)}>
          {data.title}
        </span>
      </h1>
      {data.subtitle ? (
        <p className="stage-tagline opening-tagline stage-rise" style={{ animationDelay: '0.3s' }}>
          {data.subtitle}
        </p>
      ) : null}
    </header>
  )
}

function Board({ data }: { data: PublicSpotlight }) {
  const headerRef = useRef<HTMLElement>(null)
  const [cardWidth, setCardWidth] = useState(0)
  const [active, setActive] = useState<Active | null>(null)
  const [queued, setQueued] = useState<Command | null>(null)
  const [closeKey, setCloseKey] = useState(0)
  // Cards revealed by a Show that has not finished yet stay face down in the grid until they land.
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set())
  // The newest Show already played (or skipped), and the "Hide all again" epoch seen last.
  const [handled, setHandled] = useState<number | null>(null)
  const [epoch, setEpoch] = useState<number | null>(null)
  const [seen, setSeen] = useState<PublicSpotlight | null>(null)
  const count = data.cards.length

  // Fit the grid to the screen: measured after the header lays out, again on every resize.
  useLayoutEffect(() => {
    const measure = () => {
      const head = headerRef.current?.getBoundingClientRect().height ?? 0
      setCardWidth(fitWidth(count, window.innerWidth, window.innerHeight, head))
    }
    measure()
    window.addEventListener('resize', measure)
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    if (headerRef.current) observer?.observe(headerRef.current)
    return () => {
      window.removeEventListener('resize', measure)
      observer?.disconnect()
    }
  }, [count])

  // The organiser's commands, read from each new answer: every Show once, by its number;
  // "Send back"; "Hide all again".
  if (data !== seen) {
    setSeen(data)
    const shown = data.shown
    if (handled === null) {
      // Opened now: show the cards as they are, without replaying the last Show.
      setHandled(Math.max(data.seq, shown?.seq ?? 0))
      setEpoch(data.epoch)
    } else if (epoch !== data.epoch) {
      // Every card face down again: whatever is out disappears at once.
      setEpoch(data.epoch)
      setHandled(Math.max(handled, data.seq))
      setQueued(null)
      setPending(new Set())
      setActive(null)
    } else if (shown && shown.seq > handled) {
      setHandled(shown.seq)
      if (!shown.back) {
        const next = new Set(pending)
        // A Show still waiting to start is replaced: that card simply shows face up in its place.
        if (queued && queued.id !== shown.id) next.delete(queued.id)
        if (shown.first) next.add(shown.id)
        setPending(next)
        setQueued({ id: shown.id, seq: shown.seq, first: shown.first })
        // Someone is out already: they go back first.
        if (active) setCloseKey(closeKey + 1)
      }
    } else if (shown?.back) {
      if (active && active.seq === shown.seq) setCloseKey(closeKey + 1)
      if (queued && queued.seq === shown.seq) {
        // Sent back before it came out: it simply shows face up in its place.
        setQueued(null)
        const next = new Set(pending)
        next.delete(queued.id)
        setPending(next)
      }
    }
  }

  // Start the waiting Show once nothing is out: load the picture, then fly.
  useEffect(() => {
    if (!queued || active) return
    const command = queued
    let cancelled = false
    const index = data.cards.findIndex((card) => card.id === command.id)
    const card = index >= 0 ? data.cards[index] : undefined
    const drop = () => {
      setQueued((waiting) => (waiting === command ? null : waiting))
      setPending((before) => {
        const next = new Set(before)
        next.delete(command.id)
        return next
      })
    }
    if (!card || !card.revealed) {
      drop()
      return
    }
    const person: SpotlightPerson = { id: card.id, name: card.name, detail: card.detail, image_url: card.image_url }
    void preload(card.image_url).then(() => {
      if (cancelled) return
      const slot = document.querySelector<HTMLElement>(`[data-spot-slot="${CSS.escape(card.id)}"]`)
      if (!slot) {
        drop()
        return
      }
      const box = slot.getBoundingClientRect()
      const radius = parseFloat(getComputedStyle(slot).borderTopLeftRadius) || 0
      setQueued((waiting) => (waiting === command ? null : waiting))
      setActive({ ...command, person, number: index + 1, origin: { left: box.left, top: box.top, width: box.width, height: box.height, radius } })
    })
    return () => {
      cancelled = true
    }
  }, [queued, active, data.cards])

  const closed = useCallback((id: string) => {
    // Synchronously, so the card shows in its place in the frame the flying one lands.
    flushSync(() => {
      setActive(null)
      setPending((before) => {
        if (!before.has(id)) return before
        const next = new Set(before)
        next.delete(id)
        return next
      })
    })
  }, [])

  const style = cardWidth ? ({ '--spot-w': `${cardWidth}px` } as React.CSSProperties) : undefined
  return (
    <div className="opening-inner spot-inner" data-fit={cardWidth ? '' : undefined} style={style}>
      <Header data={data} headerRef={headerRef} />
      {count ? (
        <ul aria-label="Spotlight" className="spot-grid">
          {data.cards.map((card, index) => {
            const face = card.revealed && !pending.has(card.id)
            return (
              <li
                key={card.id}
                data-spot-slot={card.id}
                data-face={face ? 'up' : 'down'}
                data-away={active?.id === card.id ? '' : undefined}
                className="spot-card stage-rise"
                style={{ animationDelay: `${0.35 + index * 0.035}s` }}
                aria-label={face ? [card.name, card.detail].filter(Boolean).join(', ') : `Card ${index + 1}, hidden`}
              >
                {face ? (
                  <SpotlightFace person={{ id: card.id, name: card.name, detail: card.detail, image_url: card.image_url }} />
                ) : (
                  <CardBack number={index + 1} />
                )}
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="stage-note mx-auto mt-8 max-w-md text-center">The cards will appear here soon.</p>
      )}
      {active ? (
        <SpotlightStage
          key={active.seq}
          person={active.person}
          number={active.number}
          origin={active.origin}
          first={active.first}
          holdMs={data.hold_seconds * 1000}
          closeKey={closeKey}
          onClosed={closed}
        />
      ) : null}
    </div>
  )
}

export function SpotlightScreen() {
  const { data, error, retry } = useSpotlight()
  const rootRef = useRef<HTMLDivElement>(null)
  const fullscreen = useIsFullscreen()
  useIdleAttribute(rootRef)
  useFullscreenStage()

  const title = data?.title
  useEffect(() => {
    if (title) document.title = `${title} · Spotlight`
  }, [title])

  return (
    <div
      ref={rootRef}
      data-scene="opening"
      data-spotlight=""
      data-fullscreen={fullscreen ? '' : undefined}
      className="opening-page relative isolate min-h-dvh"
    >
      <StageArcs />
      <StarField />
      <CoinRain />
      <FullscreenToggle />
      {data ? (
        <Board data={data} />
      ) : (
        <div className="opening-inner" aria-busy={!error}>
          <div className="opening-header">
            <div aria-hidden className="opening-emblem-placeholder" />
            {error ? (
              <div className="stage-note stage-note-danger mt-8 max-w-md text-center" role="alert">
                <p className="font-semibold">We could not load the spotlight</p>
                <p className="mt-1">{error} It tries again every second.</p>
                <button type="button" onClick={retry} className="stage-link mt-3">
                  Try again now
                </button>
              </div>
            ) : (
              <p className="sr-only" role="status">
                Loading…
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
