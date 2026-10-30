'use client'

import { Maximize, Minimize } from 'lucide-react'
import { useEffect, useSyncExternalStore } from 'react'

/*
 * Full screen for the opening page, so the projector or the LED wall shows the page without
 * the browser's bars. The whole document goes full screen (document.documentElement): the big
 * card and the media viewer are modal dialogs in the top layer and stay above it.
 *
 * A small round button in the top-right corner and the "F" key switch it. In full screen, the
 * button and the mouse cursor fade away once the pointer has rested for a moment. iPhone Safari
 * has no full screen for pages; there the button is not shown at all.
 */

/** How long the pointer must rest before the controls (and, in full screen, the cursor) fade. */
export const IDLE_MS = 2500

const onFullscreenChange = (listener: () => void) => {
  document.addEventListener('fullscreenchange', listener)
  return () => document.removeEventListener('fullscreenchange', listener)
}
const neverChanges = () => () => {}

/** Whether this browser lets the page go full screen. False on the server and during hydration. */
export function useFullscreenEnabled(): boolean {
  return useSyncExternalStore(
    neverChanges,
    () => document.fullscreenEnabled === true,
    () => false,
  )
}

/** Whether the page (or anything on it) is full screen now. */
export function useIsFullscreen(): boolean {
  return useSyncExternalStore(
    onFullscreenChange,
    () => Boolean(document.fullscreenElement),
    () => false,
  )
}

/**
 * Takes the page full screen; resolves true once it is. Call it from a click or a key press:
 * browsers allow full screen only in answer to one.
 */
export function enterFullscreen(): Promise<boolean> {
  const root = document.documentElement
  if (!document.fullscreenEnabled || document.fullscreenElement || typeof root.requestFullscreen !== 'function') {
    return Promise.resolve(false)
  }
  try {
    return Promise.resolve(root.requestFullscreen({ navigationUI: 'hide' })).then(
      () => true,
      () => false,
    )
  } catch {
    return Promise.resolve(false)
  }
}

export function leaveFullscreen() {
  if (!document.fullscreenElement) return
  try {
    void Promise.resolve(document.exitFullscreen()).catch(() => {})
  } catch {
    // Already leaving.
  }
}

const toggleFullscreen = () => {
  if (document.fullscreenElement) leaveFullscreen()
  else void enterFullscreen()
}

type KeyboardLock = { lock?: (keys?: string[]) => Promise<void>; unlock?: () => void }
const keyboard = () => (navigator as Navigator & { keyboard?: KeyboardLock }).keyboard

/**
 * Going full screen puts the page itself in the top layer, above any dialog already open (the
 * big card, the media viewer, which opens before the screen switches over): on the stage's
 * opaque background they would vanish under it. Showing them again puts them back on top, in
 * the same order (the viewer comes after the big card in the page), with focus where it was.
 */
function raiseOpenDialogs() {
  const open = [...document.querySelectorAll<HTMLDialogElement>('dialog[open][data-opening-dialog]')]
  if (open.length === 0) return
  const focused = document.activeElement
  for (const dialog of open) {
    // Their own "close" handlers ignore a close event that finds them open again.
    dialog.close()
    dialog.showModal()
  }
  if (focused instanceof HTMLElement && focused.isConnected) focused.focus({ preventScroll: true })
}

/**
 * What full screen needs on the opening page: the open dialogs stay above the page, and, in
 * Chromium, a short Escape press reaches the page, so it closes the big card or the media
 * viewer instead of leaving full screen. Holding Escape still leaves; that is the browser's
 * rule. Elsewhere the lock does nothing.
 */
export function useFullscreenStage() {
  useEffect(() => {
    const onChange = () => {
      if (document.fullscreenElement) raiseOpenDialogs()
      try {
        if (document.fullscreenElement) void keyboard()?.lock?.(['Escape']).catch(() => {})
        else keyboard()?.unlock?.()
      } catch {
        // Not allowed here: Escape keeps its usual meaning.
      }
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      try {
        keyboard()?.unlock?.()
      } catch {
        // Nothing was locked.
      }
    }
  }, [])
}

/**
 * Sets `data-idle` on the element once the pointer has rested for IDLE_MS, and removes it at
 * the next movement (or touch, or key press with `keys`). Stylesheets decide what fades. An
 * attribute React does not render, so nothing re-renders.
 */
export function useIdleAttribute(ref: React.RefObject<HTMLElement | null>, { keys = false }: { keys?: boolean } = {}) {
  useEffect(() => {
    const element = ref.current
    if (!element) return
    let timer = 0
    const wake = () => {
      if (element.hasAttribute('data-idle')) element.removeAttribute('data-idle')
      window.clearTimeout(timer)
      timer = window.setTimeout(() => element.setAttribute('data-idle', ''), IDLE_MS)
    }
    wake()
    const events = ['pointermove', 'pointerdown', 'wheel', ...(keys ? ['keydown'] : [])]
    for (const name of events) document.addEventListener(name, wake, { passive: true })
    // Entering full screen starts the count again, so the button shows for a moment first.
    document.addEventListener('fullscreenchange', wake)
    return () => {
      window.clearTimeout(timer)
      for (const name of events) document.removeEventListener(name, wake)
      document.removeEventListener('fullscreenchange', wake)
      element.removeAttribute('data-idle')
    }
  }, [ref, keys])
}

/** Typing in a field: letters are text there, not shortcuts. */
function typing(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || target.matches('input, textarea, select'))
}

/** The round button in the top-right corner, and the "F" key. */
export function FullscreenToggle() {
  const enabled = useFullscreenEnabled()
  const full = useIsFullscreen()

  useEffect(() => {
    if (!enabled) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'f' && event.key !== 'F') return
      if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return
      if (typing(event.target)) return
      // Another dialog than the page's own (the big card, the media viewer) keeps its keys.
      if (document.querySelector('dialog[open]:not([data-opening-dialog])')) return
      event.preventDefault()
      toggleFullscreen()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [enabled])

  if (!enabled) return null
  const label = full ? 'Exit full screen' : 'Full screen'
  return (
    <button
      type="button"
      className="opening-round opening-fullscreen"
      aria-label={label}
      aria-keyshortcuts="F"
      title={`${label} (F)`}
      onClick={toggleFullscreen}
    >
      {full ? <Minimize aria-hidden strokeWidth={2} /> : <Maximize aria-hidden strokeWidth={2} />}
    </button>
  )
}
