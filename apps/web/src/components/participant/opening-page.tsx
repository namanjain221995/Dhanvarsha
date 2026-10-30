'use client'

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, participantApi } from '@/lib/api'
import type { OpeningCard, PublicOpening } from '@/lib/types'
import { CoinRain } from './coin-rain'
import { FullscreenToggle, useFullscreenStage, useIdleAttribute, useIsFullscreen } from './fullscreen'
import { MediaViewerProvider } from './media-viewer'
import { CardBack, CardFace, FaceSpacer, IconGold, lengthTier } from './opening-card-face'
import { liveReveals, prunedReveals, readReveals, saveReveals, useStoredReveals, withReveal } from './reveal-store'
import { RevealStage, type RevealTarget } from './reveal-stage'
import { BrandEmblem, StageArcs } from './stage-sign-in'
import { StarField } from './star-field'

/*
 * The opening page (/opening): a public showcase for the big screen at the venue, a phone, a
 * laptop, a projector or the LED wall. The night-blue stage of the gold look with its arcs,
 * stars and coin rain; the emblem, title and year; then one card per segment of the programme.
 *
 * Everything on it comes from GET /public/opening and is fetched again every 10 seconds, so an
 * organiser's edits (and "Hide all cards again") reach the projector without anyone touching
 * it. When a refresh fails the page keeps showing what it last had. Nothing personal is ever
 * shown.
 *
 * With the page's "hidden" setting (the default) it is a game show: every card lies face down,
 * the presenter asks the room to pick one, and the card flies to the middle
 * to be scratched open (reveal-stage.tsx). Revealed cards stay revealed in this browser.
 *
 * For the presenter: a small full-screen button in the corner (and the "F" key), and a card's
 * button shows the organiser's uploaded picture or video full screen (media-viewer.tsx).
 */

const REFRESH_MS = 10_000

/*
 * On a landscape screen the whole page fits on one screen, whatever its size or zoom: a laptop
 * at 125 % in full screen is 1536 × 864 CSS pixels, a projector 1024 × 768, the LED wall
 * 1920 × 872. The page is measured in one unit (--u, globals.css): the header and the paddings
 * need about FIT_HEAD units of height, each row of cards FIT_ROW, and each column FIT_COL of
 * width plus FIT_SIDE for the sides. The column count that gives the biggest unit wins.
 */
const FIT_HEAD = 24
const FIT_ROW = 15.5
const FIT_COL = 13.7
const FIT_SIDE = 4
// Below these the page scrolls instead (phones, portrait tablets, very many cards).
const FIT_MIN_WIDTH = 900
const FIT_MIN_ASPECT = 1.15
const FIT_MIN_UNIT = 8

function fitLayout(count: number, width: number, height: number): string {
  if (count < 1 || width < FIT_MIN_WIDTH || width / height < FIT_MIN_ASPECT) return ''
  let best = { cols: 0, rows: 0, unit: 0 }
  for (let cols = 1; cols <= Math.min(count, 10); cols += 1) {
    const rows = Math.ceil(count / cols)
    const unit = Math.min(width / (FIT_SIDE + cols * FIT_COL), height / (FIT_HEAD + rows * FIT_ROW))
    if (unit > best.unit + 0.01) best = { cols, rows, unit }
  }
  return best.unit >= FIT_MIN_UNIT ? `${best.cols} ${best.rows}` : ''
}

const subscribeResize = (onChange: () => void) => {
  window.addEventListener('resize', onChange)
  return () => window.removeEventListener('resize', onChange)
}

/** "cols rows" for a one-screen layout, or '' for the scrolling one. */
function useFit(count: number) {
  const snapshot = useCallback(() => fitLayout(count, window.innerWidth, window.innerHeight), [count])
  return useSyncExternalStore(subscribeResize, snapshot, () => '')
}

function useOpening() {
  const [opening, setOpening] = useState<PublicOpening | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    let inflight: AbortController | null = null
    let last = ''
    const load = async () => {
      // One request at a time: a slow answer is not overtaken by the next refresh.
      if (inflight) return
      const controller = new AbortController()
      inflight = controller
      try {
        const data = await participantApi<PublicOpening>('/public/opening', { timeoutMs: 10_000, signal: controller.signal })
        if (!alive) return
        // Same answer as last time: nothing to re-render.
        const json = JSON.stringify(data)
        if (json !== last) {
          last = json
          setOpening(data)
        }
        setError(null)
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'Could not reach the server.')
      } finally {
        if (inflight === controller) inflight = null
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), REFRESH_MS)
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

  return { opening, error, retry: () => setAttempt((n) => n + 1) }
}

function Header({ opening }: { opening: PublicOpening }) {
  const { title, year, tagline } = opening
  return (
    <header className="opening-header">
      <div className="stage-emblem-wrap opening-emblem">
        <BrandEmblem src={opening.logo_url} />
      </div>
      <h1 className="opening-heading stage-rise" style={{ animationDelay: '0.25s' }}>
        <span className="stage-title opening-title" data-size={lengthTier(title, 14, 24)}>
          {title}
        </span>
        {year ? (
          <span className="opening-year">
            <span aria-hidden className="opening-rule" />
            {/* The leading space keeps "Dhanvarsha 2026" two words for screen readers. */}
            <span className="opening-year-text">{` ${year}`}</span>
            <span aria-hidden className="opening-rule opening-rule-end" />
          </span>
        ) : null}
      </h1>
      {tagline ? (
        <p className="stage-tagline opening-tagline stage-rise" style={{ animationDelay: '0.35s' }}>
          {tagline}
        </p>
      ) : null}
    </header>
  )
}

const rise = (index: number) => ({ animationDelay: `${0.45 + index * 0.04}s` })

/** The page as it always was: every card face up. */
function OpenCards({ opening }: { opening: PublicOpening }) {
  return (
    <ul aria-label="Programme" className="opening-grid">
      {opening.cards.map((card, index) => (
        <li key={card.id} className="opening-card stage-rise" style={rise(index)}>
          <CardFace card={card} label={opening.button_label} />
        </li>
      ))}
    </ul>
  )
}

/**
 * The game show: face-down cards. Choosing one (click, tap, Enter or Space)
 * opens it big in the middle; a revealed card shows its face here and opens big again on its face.
 */
function HiddenCards({ opening }: { opening: PublicOpening }) {
  const { cards, button_label: label } = opening
  const stored = useStoredReveals()
  const revealed = useMemo(() => liveReveals(stored, opening), [stored, opening])
  const [active, setActive] = useState<RevealTarget | null>(null)
  const [returnTo, setReturnTo] = useState<{ id: string } | null>(null)

  // Forget reveals from an earlier epoch (the organiser hid every card again) or for cards
  // that are no longer on the page.
  useEffect(() => {
    const pruned = prunedReveals(stored, opening)
    if (pruned) saveReveals(pruned)
  }, [stored, opening])

  // Back from the big card: focus returns to the card it came from.
  useEffect(() => {
    if (!returnTo) return
    document.querySelector<HTMLElement>(`[data-card-hit="${CSS.escape(returnTo.id)}"]`)?.focus({ preventScroll: true })
  }, [returnTo])

  const activeIndex = active ? cards.findIndex((card) => card.id === active.id) : -1
  // The organiser removed the card while it was out: put it away at once.
  if (active && activeIndex < 0) setActive(null)
  const activeCard = activeIndex >= 0 ? cards[activeIndex] : undefined

  const open = (card: OpeningCard, button: HTMLElement) => {
    if (active) return
    const slot = button.closest<HTMLElement>('[data-card-slot]') ?? button
    const box = slot.getBoundingClientRect()
    const radius = parseFloat(getComputedStyle(slot).borderTopLeftRadius) || 0
    setReturnTo(null)
    setActive({
      id: card.id,
      revealed: revealed.has(card.id),
      origin: { left: box.left, top: box.top, width: box.width, height: box.height, radius },
    })
  }

  const reveal = (id: string) => saveReveals(withReveal(readReveals(), opening, id))

  const closed = (id: string) => {
    // Synchronously, so the card reappears in the grid in the frame the flying one lands.
    flushSync(() => {
      setActive(null)
      setReturnTo({ id })
    })
  }

  return (
    <>
      <ul aria-label="Programme" className="opening-grid">
        {cards.map((card, index) => {
          const number = index + 1
          const face = revealed.has(card.id)
          return (
            <li
              key={card.id}
              data-card-slot={card.id}
              data-face={face ? 'up' : 'down'}
              data-away={active?.id === card.id ? '' : undefined}
              className="opening-card stage-rise"
              style={rise(index)}
            >
              {/* One button for both sides, so focus stays on it when the card turns over. */}
              <button
                type="button"
                data-card-hit={card.id}
                aria-haspopup="dialog"
                aria-label={face ? `Card ${number}: ${card.title}. Show it big.` : `Card ${number}, hidden. Open to reveal.`}
                className="opening-card-hit"
                onClick={(event) => open(card, event.currentTarget)}
              />
              {face ? (
                <CardFace card={card} label={label} />
              ) : (
                <>
                  <FaceSpacer label={label} />
                  <CardBack number={number} />
                </>
              )}
            </li>
          )
        })}
      </ul>
      {active && activeCard ? (
        <RevealStage
          key={active.id}
          card={activeCard}
          number={activeIndex + 1}
          label={label}
          target={active}
          faceInGrid={revealed.has(active.id)}
          onReveal={reveal}
          onClosed={closed}
        />
      ) : null}
    </>
  )
}

function Showcase({ opening }: { opening: PublicOpening }) {
  return (
    <div className="opening-inner">
      <Header opening={opening} />
      {opening.hidden ? <HiddenCards opening={opening} /> : <OpenCards opening={opening} />}
    </div>
  )
}

export function OpeningShowcase() {
  const { opening, error, retry } = useOpening()
  const rootRef = useRef<HTMLDivElement>(null)
  const fullscreen = useIsFullscreen()
  const [fitCols, fitRows] = useFit(opening?.cards.length ?? 0).split(' ')
  // In full screen the corner button and the cursor fade while the pointer rests (globals.css).
  useIdleAttribute(rootRef)
  useFullscreenStage()

  // The tab (and the projector's window title) follows the page's own title.
  const pageTitle = opening ? [opening.title, opening.year].filter(Boolean).join(' ') : null
  useEffect(() => {
    if (pageTitle) document.title = `${pageTitle} · Opening`
  }, [pageTitle])

  return (
    <div
      ref={rootRef}
      data-scene="opening"
      data-fullscreen={fullscreen ? '' : undefined}
      data-fit={fitRows ? '' : undefined}
      style={fitRows ? ({ '--fit-cols': fitCols, '--fit-rows': fitRows } as React.CSSProperties) : undefined}
      className="opening-page relative isolate min-h-dvh"
    >
      <MediaViewerProvider>
        <StageArcs />
        <StarField />
        <CoinRain />
        <IconGold />
        <FullscreenToggle />
        {opening ? (
          <Showcase opening={opening} />
        ) : (
          <div className="opening-inner" aria-busy={!error}>
            <div className="opening-header">
              <div aria-hidden className="opening-emblem-placeholder" />
              {error ? (
                <div className="stage-note stage-note-danger mt-8 max-w-md text-center" role="alert">
                  <p className="font-semibold">We could not load the opening page</p>
                  <p className="mt-1">{error} It tries again every 10 seconds.</p>
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
      </MediaViewerProvider>
    </div>
  )
}
