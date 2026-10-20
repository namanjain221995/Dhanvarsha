'use client'

/**
 * Card-to-detail transition using the View Transitions API where the browser supports
 * it. The callback navigates; the transition waits until the destination page reports
 * that it has mounted (or 700ms), so the shared logo and title morph into place.
 * Browsers without support, and people who prefer reduced motion, navigate instantly.
 */

let pending: (() => void) | null = null

export function notifyPageReady() {
  pending?.()
  pending = null
}

type ViewTransitionDocument = Document & {
  startViewTransition?: (cb: () => Promise<void> | void) => { finished: Promise<void> }
}

export function navigateWithTransition(navigate: () => void) {
  const doc = document as ViewTransitionDocument
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (!doc.startViewTransition || reduce) {
    navigate()
    return
  }
  doc.startViewTransition(
    () =>
      new Promise<void>((resolve) => {
        const timer = window.setTimeout(() => {
          pending = null
          resolve()
        }, 700)
        pending = () => {
          window.clearTimeout(timer)
          resolve()
        }
        navigate()
      }),
  )
}

export function transitionName(kind: 'logo' | 'title' | 'card', eventId: string): string {
  return `sv-${kind}-${eventId}`
}
