'use client'

import { ArrowUpRight, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import type { OpeningCard } from '@/lib/types'
import { useOpenMedia } from './media-viewer'
import { OpeningIcon } from './opening-icons'

/*
 * The two sides of an opening card, shared by the grid and the big card of the reveal: the
 * face (gold-ringed icon or picture, the title, the gold button) and the back (gold foil with
 * the card's number, for the game-show reveal).
 */

/**
 * Only links the API allows are followed: http(s) addresses and paths on this site. Never
 * "//host" or a backslash, which browsers read as another site.
 */
export function safeHref(link: string): string | null {
  const value = link.trim()
  if (/^https?:\/\/[^\s\\/?#]+[^\s\\]*$/i.test(value)) return value
  if (/^\/(?!\/)[^\s\\]*$/.test(value)) return value
  return null
}

/** Long names step down in size so they keep to their lines (the page must fit one screen). */
export function lengthTier(text: string, long: number, longer: number): 'long' | 'longer' | undefined {
  if (text.length > longer) return 'longer'
  if (text.length > long) return 'long'
  return undefined
}

/** Gold for the card icons: one gradient in the icons' own 24-unit space, shared by every card. */
export function IconGold() {
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

const newTab = (href: string) => (href.startsWith('/') ? {} : { target: '_blank', rel: 'noopener noreferrer' })

/** The round gold arrow beside the pill of a card with a picture or video and a link too. */
function LinkArrow({ card, href }: { card: OpeningCard; href: string }) {
  const external = !href.startsWith('/')
  return (
    <a
      href={href}
      data-opening-link=""
      title="Open the link"
      aria-label={`Open the link for ${card.title}${external ? ' (opens in a new tab)' : ''}`}
      className="opening-link"
      {...newTab(href)}
    >
      <ArrowUpRight aria-hidden className="opening-link-arrow" strokeWidth={2.5} />
    </a>
  )
}

/**
 * The gold pill. With an uploaded picture or video it is a button that shows it full screen
 * (media-viewer.tsx), and a link as well gets a round arrow beside it; otherwise, with a link,
 * the pill is a real link (other sites open in a new tab); without either it is decoration
 * only, so it never looks like a broken button and never takes focus.
 */
function PlayButton({ card, label, actionRef }: { card: OpeningCard; label: string; actionRef?: React.RefCallback<HTMLElement> }) {
  const openMedia = useOpenMedia()
  const href = safeHref(card.link)
  const face = (
    <>
      <span>{label}</span>
      <ChevronRight aria-hidden className="opening-play-chevron" strokeWidth={2.75} />
    </>
  )
  if (card.media && openMedia) {
    const show = (
      <button
        ref={actionRef}
        type="button"
        data-variant="gold"
        data-opening-play=""
        data-media={card.media.type}
        aria-haspopup="dialog"
        className="opening-play"
        onClick={(event) => openMedia(card, event.currentTarget)}
      >
        {face}
        <span className="sr-only">
          : {card.title} ({card.media.type === 'video' ? 'video' : 'picture'})
        </span>
      </button>
    )
    if (!href) return show
    return (
      <div className="opening-actions">
        {show}
        <LinkArrow card={card} href={href} />
      </div>
    )
  }
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
      ref={actionRef}
      href={href}
      data-variant="gold"
      data-opening-play=""
      className="opening-play"
      {...newTab(href)}
    >
      {face}
      <span className="sr-only">
        : {card.title}
        {external ? ' (opens in a new tab)' : ''}
      </span>
    </a>
  )
}

/** The face: icon, title and button, laid out by the parent (a grid card or the big card). */
export function CardFace({
  card,
  label,
  titleId,
  titleRef,
  actionRef,
}: {
  card: OpeningCard
  label: string
  titleId?: string
  titleRef?: React.Ref<HTMLHeadingElement>
  /** The button or link, when the face has one (the big card focuses it after a reveal). */
  actionRef?: React.RefCallback<HTMLElement>
}) {
  return (
    <>
      <CardEmblem card={card} />
      <div className="opening-card-head">
        <h2
          id={titleId}
          ref={titleRef}
          tabIndex={titleRef ? -1 : undefined}
          className="opening-card-title"
          data-size={lengthTier(card.title, 26, 38)}
        >
          <span>{card.title}</span>
        </h2>
      </div>
      <PlayButton card={card} label={label} actionRef={actionRef} />
    </>
  )
}

/**
 * The same height as a face, with nothing in it: a face-down card keeps the size of a revealed
 * one, so turning a card over never moves the others. The button's label is the same on every
 * card, so it gives nothing away.
 */
export function FaceSpacer({ label }: { label: string }) {
  return (
    <>
      <span aria-hidden className="opening-icon opening-ghost" />
      <span aria-hidden className="opening-card-head opening-ghost" />
      <span aria-hidden className="opening-play opening-ghost">
        {label}
      </span>
    </>
  )
}

// Where the twinkling stars sit on each back: fixed per card number, so every screen agrees.
function twinkles(number: number) {
  let seed = 7919 * number + 104729
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
  const spots = [
    [18, 16],
    [82, 24],
    [24, 84],
    [78, 80],
  ]
  // Three of the four corners, a little off their spot, each on its own clock.
  const skip = Math.floor(rand() * spots.length)
  return spots
    .filter((_, i) => i !== skip)
    .map(([x, y]) => ({
      x: x! + (rand() - 0.5) * 10,
      y: y! + (rand() - 0.5) * 10,
      cycle: 3.4 + rand() * 2.8,
      delay: -rand() * 6,
      size: 0.75 + rand() * 0.5,
    }))
}

/**
 * The back of a card: gold foil with a slow shine, a few twinkling stars and the TechSara mark
 * in a gold ring. It never carries the card's title, icon or number. Every size is relative to the card's width (cqw), so the
 * small card in the grid and the big one in the middle are the same picture.
 */
export function CardBack({ number, className = '' }: { number: number; className?: string }) {
  return (
    <span aria-hidden className={`reveal-back ${className}`} style={{ '--shine-delay': `${(number - 1) * 0.42}s` } as React.CSSProperties}>
      <span className="reveal-back-frame" />
      <span className="reveal-back-shine" />
      {twinkles(number).map((star, i) => (
        <span
          key={i}
          className="reveal-twinkle"
          style={
            {
              left: `${star.x}%`,
              top: `${star.y}%`,
              '--cycle': `${star.cycle}s`,
              '--delay': `${star.delay}s`,
              '--twinkle-size': star.size,
            } as React.CSSProperties
          }
        />
      ))}
      <span className="reveal-back-mark">
        <span className="reveal-back-disc">
          {/* eslint-disable-next-line @next/next/no-img-element -- a static file from public/ */}
          <img src="/brand/techsara-mark.png" alt="" decoding="async" draggable={false} />
        </span>
      </span>
    </span>
  )
}
