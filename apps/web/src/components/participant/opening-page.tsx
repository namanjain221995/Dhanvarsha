'use client'

import { ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApiError, participantApi } from '@/lib/api'
import type { OpeningCard, PublicOpening } from '@/lib/types'
import { CoinRain } from './coin-rain'
import { OpeningIcon } from './opening-icons'
import { BrandEmblem, StageArcs } from './stage-sign-in'
import { StarField } from './star-field'

/*
 * The opening page (/opening): a public showcase for the big screen at the venue, a phone, a
 * laptop, a projector or the LED wall. The night-blue stage of the gold look with its arcs,
 * stars and coin rain; the emblem, title and year; then one card per segment of the programme.
 *
 * Everything on it comes from GET /public/opening and is fetched again every 20 seconds, so an
 * organiser's edits reach the projector without anyone touching it. When a refresh fails the
 * page keeps showing what it last had. Nothing personal is ever shown.
 */

const REFRESH_MS = 20_000

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

/**
 * Only links the API allows are followed: http(s) addresses and paths on this site. Never
 * "//host" or a backslash, which browsers read as another site.
 */
function safeHref(link: string): string | null {
  const value = link.trim()
  if (/^https?:\/\/[^\s\\/?#]+[^\s\\]*$/i.test(value)) return value
  if (/^\/(?!\/)[^\s\\]*$/.test(value)) return value
  return null
}

/** Long names step down in size so they keep to their lines (the page must fit one screen). */
function lengthTier(text: string, long: number, longer: number): 'long' | 'longer' | undefined {
  if (text.length > longer) return 'longer'
  if (text.length > long) return 'long'
  return undefined
}

/** Gold for the card icons: one gradient in the icons' own 24-unit space, shared by every card. */
function IconGold() {
  return (
    <svg width="0" height="0" className="absolute" aria-hidden focusable="false">
      <defs>
        <linearGradient id="opening-gold" gradientUnits="userSpaceOnUse" x1="4" y1="2" x2="20" y2="22">
          <stop offset="0" stopColor="#fff1b5" />
          <stop offset="0.4" stopColor="#f6d26b" />
          <stop offset="0.75" stopColor="#dba62d" />
          <stop offset="1" stopColor="#f4cb52" />
        </linearGradient>
      </defs>
    </svg>
  )
}

function CardEmblem({ card }: { card: OpeningCard }) {
  const [failed, setFailed] = useState<string | null>(null)
  const image = card.image_url && failed !== card.image_url ? card.image_url : null
  return (
    <span aria-hidden className="opening-icon">
      <span className="opening-icon-disc">
        {image ? (
          // eslint-disable-next-line @next/next/no-img-element -- images are pre-optimised WebP from the API
          <img src={image} alt="" decoding="async" className="opening-icon-image" onError={() => setFailed(image)} />
        ) : (
          <OpeningIcon name={card.icon} className="opening-icon-glyph" color="url(#opening-gold)" strokeWidth={1.6} />
        )}
      </span>
    </span>
  )
}

/**
 * The gold pill. With a link it is a real link (other sites open in a new tab); without one it
 * is decoration only, so it never looks like a broken button and never takes focus.
 */
function PlayButton({ card, label }: { card: OpeningCard; label: string }) {
  const href = safeHref(card.link)
  const face = (
    <>
      <span>{label}</span>
      <ChevronRight aria-hidden className="opening-play-chevron" strokeWidth={2.75} />
    </>
  )
  if (!href) {
    return (
      <span aria-hidden data-variant="gold" data-opening-play="" className="opening-play">
        {face}
      </span>
    )
  }
  const external = !href.startsWith('/')
  return (
    <a
      href={href}
      data-variant="gold"
      data-opening-play=""
      className="opening-play"
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
    >
      {face}
      <span className="sr-only">
        : {card.title}
        {external ? ' (opens in a new tab)' : ''}
      </span>
    </a>
  )
}

function Showcase({ opening }: { opening: PublicOpening }) {
  const { title, year, tagline, button_label: label, cards } = opening
  return (
    <div className="opening-inner">
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

      <ul aria-label="Programme" className="opening-grid">
        {cards.map((card, index) => (
          <li key={card.id} className="opening-card stage-rise" style={{ animationDelay: `${0.45 + index * 0.04}s` }}>
            <CardEmblem card={card} />
            <div className="opening-card-head">
              <h2 className="opening-card-title" data-size={lengthTier(card.title, 26, 38)}>
                <span>{card.title}</span>
              </h2>
            </div>
            <PlayButton card={card} label={label} />
          </li>
        ))}
      </ul>
    </div>
  )
}

export function OpeningShowcase() {
  const { opening, error, retry } = useOpening()

  // The tab (and the projector's window title) follows the page's own title.
  const pageTitle = opening ? [opening.title, opening.year].filter(Boolean).join(' ') : null
  useEffect(() => {
    if (pageTitle) document.title = `${pageTitle} · Opening`
  }, [pageTitle])

  return (
    <div data-scene="opening" className="opening-page relative isolate min-h-dvh">
      <StageArcs />
      <StarField />
      <CoinRain />
      <IconGold />
      {opening ? (
        <Showcase opening={opening} />
      ) : (
        <div className="opening-inner" aria-busy={!error}>
          <div className="opening-header">
            <div aria-hidden className="opening-emblem-placeholder" />
            {error ? (
              <div className="stage-note stage-note-danger mt-8 max-w-md text-center" role="alert">
                <p className="font-semibold">We could not load the opening page</p>
                <p className="mt-1">{error} It tries again every 20 seconds.</p>
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
