'use client'

import { createContext, use } from 'react'

/*
 * The spotlight on the opening page. A card whose link is /spotlight does not leave the page:
 * its button turns the opening cards over, in a wave from the card pressed, and deals the
 * spotlight's cards in their place (opening-page.tsx). Here: the hook-up for the card's button,
 * and the card-flip waves both directions use. Transform and opacity only (plus a brief
 * brightness flash), so it stays smooth on a projector's laptop.
 */

export const SPOTLIGHT_PATH = '/spotlight'

/** A card link that opens the spotlight: "/spotlight", with or without a trailing slash. */
export function isSpotlightLink(link: string | null | undefined): boolean {
  return (link ?? '').trim().replace(/\/+$/, '') === SPOTLIGHT_PATH
}

/** Turns the opening page into the spotlight; `from` is the button pressed. */
export type OpenSpotlight = (from: HTMLElement) => void

export const OpenSpotlightContext = createContext<OpenSpotlight | null>(null)

/** Null outside the opening page: there a /spotlight link is an ordinary link. */
export function useOpenSpotlight(): OpenSpotlight | null {
  return use(OpenSpotlightContext)
}

export type Point = { x: number; y: number }

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

const centre = (element: Element): Point => {
  const box = element.getBoundingClientRect()
  return { x: box.left + box.width / 2, y: box.top + box.height / 2 }
}

export const centreOf = (element: Element | null | undefined): Point =>
  element ? centre(element) : { x: window.innerWidth / 2, y: window.innerHeight / 2 }

/** Each element's start, in ms after `delay`: nearer the origin first, the farthest at `spread`. */
function delays(elements: Element[], origin: Point, delay: number, spread: number): number[] {
  const distances = elements.map((element) => {
    const point = centre(element)
    return Math.hypot(point.x - origin.x, point.y - origin.y)
  })
  const far = Math.max(1, ...distances)
  return distances.map((distance) => delay + (distance / far) * spread)
}

const settled = (animations: Animation[]) =>
  Promise.allSettled(animations.map((animation) => animation.finished)).then(() => undefined)

/** Cards turn away on their vertical axis, with a flash of light as they go edge-on. */
export function flipAway(elements: Element[], origin: Point, { delay = 0, spread = 360 } = {}): Promise<void> {
  if (elements.length === 0) return Promise.resolve()
  const still = reducedMotion()
  const starts = delays(elements, origin, delay, still ? 0 : spread)
  return settled(
    elements.map((element, i) =>
      element.animate(
        still
          ? [{ opacity: 1 }, { opacity: 0 }]
          : [
              { transform: 'perspective(1100px) rotateY(0deg) scale(1)', opacity: 1, filter: 'brightness(1)' },
              { opacity: 1, filter: 'brightness(1.55)', offset: 0.6 },
              { transform: 'perspective(1100px) rotateY(88deg) scale(0.9)', opacity: 0, filter: 'brightness(1.9)' },
            ],
        { duration: still ? 220 : 460, delay: starts[i], easing: 'cubic-bezier(0.5, 0, 0.75, 0)', fill: 'forwards' },
      ),
    ),
  )
}

/** Cards turn in from edge-on and land with a little pop: the other side of flipAway. */
export function flipIn(elements: Element[], origin: Point, { delay = 0, spread = 420 } = {}): Promise<void> {
  if (elements.length === 0) return Promise.resolve()
  const still = reducedMotion()
  const starts = delays(elements, origin, delay, still ? 0 : spread)
  return settled(
    elements.map((element, i) =>
      element.animate(
        still
          ? [{ opacity: 0 }, { opacity: 1 }]
          : [
              {
                transform: 'perspective(1100px) rotateY(-88deg) scale(0.86) translateY(14px)',
                opacity: 0,
                filter: 'brightness(1.9)',
              },
              { opacity: 1, filter: 'brightness(1.5)', offset: 0.3 },
              { transform: 'perspective(1100px) rotateY(0deg) scale(1) translateY(0)', opacity: 1, filter: 'brightness(1)' },
            ],
        { duration: still ? 240 : 720, delay: starts[i], easing: 'cubic-bezier(0.22, 1.18, 0.36, 1)', fill: 'backwards' },
      ),
    ),
  )
}

/** A header leaves upwards into a soft blur, or arrives from it. */
export function fadeHeader(element: Element | null | undefined, direction: 'out' | 'in', delay = 0): Promise<void> {
  if (!element) return Promise.resolve()
  const still = reducedMotion()
  const away = still
    ? { opacity: 0 }
    : { opacity: 0, transform: 'translateY(-14px) scale(0.97)', filter: 'blur(8px)' }
  const home = still ? { opacity: 1 } : { opacity: 1, transform: 'none', filter: 'blur(0)' }
  const animation = element.animate(direction === 'out' ? [home, away] : [away, home], {
    duration: still ? 200 : direction === 'out' ? 420 : 620,
    delay,
    easing: direction === 'out' ? 'cubic-bezier(0.5, 0, 0.75, 0)' : 'cubic-bezier(0.2, 0.8, 0.2, 1)',
    fill: direction === 'out' ? 'forwards' : 'backwards',
  })
  return animation.finished.then(
    () => undefined,
    () => undefined,
  )
}
