'use client'

import { X } from 'lucide-react'
import { useCallback, useEffect, useEffectEvent, useId, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { OpeningCard } from '@/lib/types'
import { CardBack, CardFace } from './opening-card-face'
import { RevealBurst } from './reveal-burst'
import { ScratchFoil } from './scratch-foil'
import { lockScroll } from './scroll-lock'

/*
 * The game-show moment of the opening page: a card leaves its place in the grid, flies to the
 * middle of the screen and grows (a FLIP animation, transform only), the presenter scratches
 * its gold foil away, and the card pops with a burst of coins. Closing flies it back to its
 * place, which then shows the revealed face.
 *
 * A native modal <dialog>: the rest of the page is inert, focus stays inside, Escape closes.
 * Its label is "Card N" and it says nothing more until the card is revealed; the face (title,
 * icon, button) is not even in the page until the presenter starts scratching or uses the
 * Reveal button, the keyboard and screen-reader path.
 */

export type Box = { left: number; top: number; width: number; height: number }

/** Where a card sits in the grid, with its corner radius (the big card keeps the proportion). */
export type Slot = Box & { radius: number }

/** The card that is out of the grid: where it came from and whether it was revealed then. */
export type RevealTarget = { id: string; revealed: boolean; origin: Slot }

const OPEN_MS = 460
const CLOSE_MS = 400
/** A slight overshoot, so the card lands with a little pop. */
const POP = 'cubic-bezier(0.3, 1.32, 0.55, 1)'
const GLIDE = 'cubic-bezier(0.45, 0, 0.2, 1)'

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * The big card: the grid card's shape, about min(86vw, 62vh × aspect) wide, so it is large on a
 * projector and fits a phone in either orientation.
 */
function bigSize(origin: Box, vw: number, vh: number) {
  const aspect = origin.width > 0 && origin.height > 0 ? origin.width / origin.height : 0.85
  const width = Math.max(120, Math.min(vw * 0.86, vh * 0.62 * aspect))
  return { width: Math.round(width), height: Math.round(width / aspect) }
}

/** The transform that puts an element laid out at `to` exactly over `from`. */
function flip(from: Box, to: DOMRect): string {
  const dx = from.left + from.width / 2 - (to.left + to.width / 2)
  const dy = from.top + from.height / 2 - (to.top + to.height / 2)
  return `translate(${dx}px, ${dy}px) scale(${from.width / to.width}, ${from.height / to.height})`
}

export function RevealStage({
  card,
  number,
  label,
  target,
  faceInGrid,
  onReveal,
  onClosed,
}: {
  card: OpeningCard
  number: number
  label: string
  target: RevealTarget
  /** Whether the grid shows this card's face now (the organiser may hide all cards meanwhile). */
  faceInGrid: boolean
  onReveal: (id: string) => void
  onClosed: (id: string) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const backdropRef = useRef<HTMLDivElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const backRef = useRef<HTMLSpanElement>(null)
  const foilRef = useRef<HTMLDivElement>(null)
  const auraRef = useRef<HTMLSpanElement>(null)
  const actionsRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const revealRef = useRef<HTMLButtonElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const actionRef = useRef<HTMLElement | null>(null)
  const flightRef = useRef<Animation | null>(null)
  const closingRef = useRef(false)
  const titleId = useId()

  const [viewport, setViewport] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }))
  const [revealed, setRevealed] = useState(target.revealed)
  // The face is put in the page only once someone starts scratching (or reveals).
  const [armed, setArmed] = useState(target.revealed)
  const [foilGone, setFoilGone] = useState(target.revealed)
  const [burst, setBurst] = useState(0)

  const size = bigSize(target.origin, viewport.w, viewport.h)

  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight })
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // The face's button or link, when it has one: focus goes there after a reveal.
  const setAction = useCallback((element: HTMLElement | null) => {
    actionRef.current = element
  }, [])
  const focusFace = () => (actionRef.current ?? titleRef.current)?.focus({ preventScroll: true })

  const requestClose = () => {
    if (closingRef.current) return
    closingRef.current = true
    const cardEl = cardRef.current
    const backdrop = backdropRef.current
    if (!cardEl || !backdrop) {
      onClosed(card.id)
      return
    }
    const still = reducedMotion()
    // Carry on from wherever the card is now, even in the middle of its flight or its pop.
    const from = getComputedStyle(cardEl).transform
    for (const animation of cardEl.getAnimations()) animation.cancel()
    flightRef.current = null

    // A card that is not revealed in the grid lands face down: its back comes up over the foil.
    const back = backRef.current
    if (back && !faceInGrid) {
      const opacity = getComputedStyle(back).opacity
      for (const animation of back.getAnimations()) animation.cancel()
      back.style.visibility = ''
      back.animate([{ opacity }, { opacity: 1 }], { duration: still ? 120 : 180, easing: 'ease-out', fill: 'forwards' })
    }

    const slot = document.querySelector<HTMLElement>(`[data-card-slot="${CSS.escape(card.id)}"]`)
    const home = slot?.getBoundingClientRect()
    let away: Animation
    if (still || !home || home.width === 0) {
      away = cardEl.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, easing: 'ease-in', fill: 'forwards' })
    } else {
      const to = flip({ left: home.left, top: home.top, width: home.width, height: home.height }, cardEl.getBoundingClientRect())
      away = cardEl.animate([{ transform: from }, { transform: to }], { duration: CLOSE_MS, easing: GLIDE, fill: 'forwards' })
    }
    backdrop.animate([{ opacity: getComputedStyle(backdrop).opacity }, { opacity: 0 }], {
      duration: still ? 200 : CLOSE_MS,
      easing: 'ease-in',
      fill: 'forwards',
    })
    for (const element of [actionsRef.current, closeRef.current]) {
      if (!element) continue
      const opacity = getComputedStyle(element).opacity
      for (const animation of element.getAnimations()) animation.cancel()
      element.animate([{ opacity }, { opacity: 0 }], { duration: 160, easing: 'ease-in', fill: 'forwards' })
    }
    away.finished.then(
      () => onClosed(card.id),
      () => {},
    )
  }

  const reveal = () => {
    // On its way back the card stays as it was: a scratch finished in flight does not count.
    if (revealed || closingRef.current) return
    flushSync(() => {
      setRevealed(true)
      setArmed(true)
    })
    onReveal(card.id)
    focusFace()
    const still = reducedMotion()
    const celebrate = () => {
      const foil = foilRef.current
      if (foil) {
        foil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: still ? 200 : 520, easing: 'ease-out', fill: 'forwards' }).finished.then(
          () => setFoilGone(true),
          () => {},
        )
      } else {
        setFoilGone(true)
      }
      if (still || closingRef.current) return
      cardRef.current?.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.06)', offset: 0.32 }, { transform: 'scale(1)' }], {
        duration: 560,
        easing: 'cubic-bezier(0.3, 0.7, 0.4, 1)',
      })
      auraRef.current?.animate(
        [
          { opacity: 0, transform: 'scale(0.75)' },
          { opacity: 1, transform: 'scale(1.02)', offset: 0.28 },
          { opacity: 0, transform: 'scale(1.3)' },
        ],
        { duration: 1300, easing: 'ease-out' },
      )
      setBurst((n) => n + 1)
    }
    // Revealed in the middle of its flight (Enter pressed at once): pop once it has landed.
    const flight = flightRef.current
    if (flight && flight.playState === 'running') {
      flight.finished.then(celebrate, () => {})
    } else {
      celebrate()
    }
  }

  const opened = useEffectEvent(() => ({ origin: target.origin, wasRevealed: target.revealed }))
  const escape = useEffectEvent(() => requestClose())
  const closedByBrowser = useEffectEvent(() => {
    closingRef.current = true
    onClosed(card.id)
  })
  const focusStart = useEffectEvent((wasRevealed: boolean) => {
    if (wasRevealed) focusFace()
    else revealRef.current?.focus({ preventScroll: true })
  })

  // Open: show the modal, then fly the card from its place in the grid to the middle.
  useLayoutEffect(() => {
    const dialog = dialogRef.current
    const cardEl = cardRef.current
    const backdrop = backdropRef.current
    if (!dialog || !cardEl || !backdrop) return
    const { origin, wasRevealed } = opened()
    closingRef.current = false
    if (!dialog.open) dialog.showModal()
    const unlock = lockScroll()
    const still = reducedMotion()
    const animations: Animation[] = []

    animations.push(
      backdrop.animate([{ opacity: 0 }, { opacity: 1 }], { duration: still ? 200 : 340, easing: 'ease-out', fill: 'backwards' }),
    )
    // The Reveal and close buttons come in as the card lands.
    for (const element of [actionsRef.current, closeRef.current]) {
      if (!element) continue
      animations.push(
        element.animate([{ opacity: 0 }, { opacity: 1 }], {
          duration: 240,
          delay: still ? 0 : OPEN_MS - 160,
          easing: 'ease-out',
          fill: 'backwards',
        }),
      )
    }
    if (still) {
      animations.push(cardEl.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: 'ease-out', fill: 'backwards' }))
    } else {
      const flight = cardEl.animate([{ transform: flip(origin, cardEl.getBoundingClientRect()) }, { transform: 'none' }], {
        duration: OPEN_MS,
        easing: POP,
        fill: 'backwards',
      })
      flightRef.current = flight
      animations.push(flight)
    }
    const back = backRef.current
    if (back && !wasRevealed) {
      // The back flies in, then fades into the scratch foil underneath it.
      const fade = back.animate([{ opacity: 1 }, { opacity: 0 }], {
        duration: still ? 160 : 280,
        delay: still ? 0 : OPEN_MS - 140,
        easing: 'ease-in-out',
        fill: 'both',
      })
      fade.finished.then(
        () => {
          // Out of sight: its shine and stars stop costing anything.
          if (!closingRef.current) back.style.visibility = 'hidden'
        },
        () => {},
      )
      animations.push(fade)
    }
    focusStart(wasRevealed)

    const onCancel = (event: Event) => {
      // Escape: fly back instead of vanishing. A browser may refuse to let a page hold the
      // dialog open on a repeated Escape; then "close" below tidies up.
      if (!event.cancelable) return
      event.preventDefault()
      escape()
    }
    const onClose = () => {
      // Also fired late for a close() made while React re-ran this effect: ignore it if open.
      if (!dialog.open) closedByBrowser()
    }
    dialog.addEventListener('cancel', onCancel)
    dialog.addEventListener('close', onClose)
    return () => {
      dialog.removeEventListener('cancel', onCancel)
      dialog.removeEventListener('close', onClose)
      for (const animation of animations) animation.cancel()
      flightRef.current = null
      unlock()
      if (dialog.open) dialog.close()
    }
  }, [])

  return (
    <dialog ref={dialogRef} className="reveal-dialog" aria-label={`Card ${number}`} aria-modal="true" data-opening-dialog="">
      <div ref={backdropRef} className="reveal-backdrop" onClick={requestClose} />
      <div
        className="reveal-stage"
        style={
          {
            width: size.width,
            height: size.height,
            '--big-radius': `${Math.round((target.origin.radius * size.width) / Math.max(1, target.origin.width))}px`,
          } as React.CSSProperties
        }
      >
        <span ref={auraRef} aria-hidden className="reveal-aura" />
        <div ref={cardRef} className="reveal-card" data-revealed={revealed ? '' : undefined}>
          <div className="reveal-face" inert={!revealed} aria-hidden={revealed ? undefined : true}>
            {armed ? <CardFace card={card} label={label} titleId={titleId} titleRef={titleRef} actionRef={setAction} /> : null}
          </div>
          {foilGone ? null : (
            <div ref={foilRef} className="reveal-foil">
              <ScratchFoil
                width={size.width}
                height={size.height}
                onStart={() => flushSync(() => setArmed(true))}
                onComplete={reveal}
              />
            </div>
          )}
          <span ref={backRef} className="reveal-flight" style={target.revealed ? { opacity: 0, visibility: 'hidden' } : undefined}>
            <CardBack number={number} />
          </span>
        </div>
        <div ref={actionsRef} className="reveal-actions">
          {revealed ? null : (
            <button ref={revealRef} type="button" className="reveal-button" onClick={reveal}>
              Reveal
            </button>
          )}
          <p role="status" className="sr-only">
            {revealed ? `Card ${number}: ${card.title}` : ''}
          </p>
        </div>
      </div>
      <button ref={closeRef} type="button" className="reveal-close" aria-label="Close" onClick={requestClose}>
        <X aria-hidden strokeWidth={2.25} />
      </button>
      <RevealBurst fireKey={burst} anchor={cardRef} />
    </dialog>
  )
}
