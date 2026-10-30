'use client'

import { ArrowUpRight, Play, X } from 'lucide-react'
import { createContext, use, useCallback, useEffectEvent, useId, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { OpeningCard, OpeningMedia } from '@/lib/types'
import { enterFullscreen, leaveFullscreen, useIdleAttribute } from './fullscreen'
import { newTab, safeHref } from './opening-link'
import { lockScroll } from './scroll-lock'

/*
 * The media viewer of the opening page. A card whose organiser uploaded a picture or a video
 * has a real button; pressing it shows that picture or video on black, as large as the screen
 * allows, above everything (also above the big card of the reveal). A native modal <dialog>:
 * the page behind is inert, Escape closes it, and focus goes back to the button afterwards.
 *
 * Opening it also takes the page full screen when it is not already (the click is the gesture
 * browsers ask for); closing it gives full screen back only if the viewer took it, and when the
 * browser leaves that full screen (Escape held) the viewer closes too. What it shows is fixed
 * when it opens, so the page's 10-second refresh never closes it or restarts a video.
 *
 * A card with a link as well gets a round gold arrow at the bottom of its picture or video,
 * which opens the link (other sites in a new tab).
 */

/** Whether the open viewer took the page full screen, shared with the click that opened it. */
type Takeover = { owns: boolean; over: boolean }

type Shown = { media: OpeningMedia; title: string; link: string | null; opener: HTMLElement }

export type OpenMedia = (card: OpeningCard, opener: HTMLElement) => void

const OpenMediaContext = createContext<OpenMedia | null>(null)

/** Shows a card's picture or video; null outside the opening page, where the button stays a link. */
export function useOpenMedia(): OpenMedia | null {
  return use(OpenMediaContext)
}

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

export function MediaViewerProvider({ children }: { children: React.ReactNode }) {
  const [shown, setShown] = useState<Shown | null>(null)
  const takeover = useRef<Takeover | null>(null)

  const open = useCallback<OpenMedia>((card, opener) => {
    const media = card.media
    if (!media || takeover.current) return
    const current: Takeover = { owns: false, over: false }
    takeover.current = current
    if (!document.fullscreenElement) {
      // Here, in the click itself: browsers allow full screen only in answer to one.
      void enterFullscreen().then((entered) => {
        if (!entered) return
        // Closed before the screen switched over: give full screen straight back.
        if (current.over) leaveFullscreen()
        else current.owns = true
      })
    }
    setShown({ media: { url: media.url, type: media.type }, title: card.title, link: safeHref(card.link), opener })
  }, [])

  /** The viewer is closing: full screen goes back if the viewer took it (and `leave`). */
  const release = useCallback((leave: boolean) => {
    const current = takeover.current
    if (!current) return
    current.over = true
    if (current.owns && leave) leaveFullscreen()
    current.owns = false
  }, [])

  const ownsFullscreen = useCallback(() => takeover.current?.owns === true, [])

  const closed = useCallback(() => {
    takeover.current = null
    setShown(null)
  }, [])

  return (
    <OpenMediaContext value={open}>
      {children}
      {shown ? <MediaViewer shown={shown} release={release} ownsFullscreen={ownsFullscreen} onClosed={closed} /> : null}
    </OpenMediaContext>
  )
}

function MediaViewer({
  shown,
  release,
  ownsFullscreen,
  onClosed,
}: {
  shown: Shown
  release: (leave: boolean) => void
  ownsFullscreen: () => boolean
  onClosed: () => void
}) {
  const { media, title, link, opener } = shown
  const titleId = useId()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const closingRef = useRef(false)
  /** Width over height, once known: the picture or video then gets exactly its own box. */
  const [ratio, setRatio] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [blocked, setBlocked] = useState(false)

  // In full screen, the close button and the title fade once the pointer rests (globals.css).
  useIdleAttribute(dialogRef, { keys: true })

  const fit = (width: number, height: number) => setRatio(width > 0 && height > 0 ? width / height : null)

  const finish = () => {
    // Out of the page first: focus cannot reach the button while this dialog is still modal.
    flushSync(onClosed)
    if (opener.isConnected) opener.focus({ preventScroll: true })
  }

  const requestClose = () => {
    if (closingRef.current) return
    closingRef.current = true
    videoRef.current?.pause()
    release(true)
    const dialog = dialogRef.current
    if (!dialog) {
      finish()
      return
    }
    const opacity = getComputedStyle(dialog).opacity
    for (const animation of dialog.getAnimations()) animation.cancel()
    dialog
      .animate([{ opacity }, { opacity: 0 }], { duration: reducedMotion() ? 120 : 200, easing: 'ease-in', fill: 'forwards' })
      .finished.then(finish, finish)
  }

  const onClick = (event: React.MouseEvent<HTMLDialogElement>) => {
    // A click on the black around the picture or video closes; the media, the buttons and the
    // link do not (the video's own controls count as the video).
    if (event.target instanceof Element && event.target.closest('img, video, button, a')) return
    requestClose()
  }

  /** The picture comes in with a gentle zoom; a plain fade with reduced motion. */
  const appear = (element: HTMLElement) => {
    const still = reducedMotion()
    element.animate(
      still
        ? [{ opacity: 0 }, { opacity: 1 }]
        : [
            { opacity: 0, transform: 'scale(0.94)' },
            { opacity: 1, transform: 'none' },
          ],
      { duration: still ? 200 : 520, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' },
    )
  }

  const escape = useEffectEvent(() => requestClose())
  const closedByBrowser = useEffectEvent(() => {
    if (closingRef.current) return
    closingRef.current = true
    videoRef.current?.pause()
    release(true)
    finish()
  })
  const fullscreenChanged = useEffectEvent(() => {
    // The viewer took full screen and the browser gave it back (Escape held, or F): close too.
    if (document.fullscreenElement || !ownsFullscreen()) return
    release(false)
    requestClose()
  })
  const playRefused = useEffectEvent(() => setBlocked(true))

  // Open: show the modal and fade it in; a video starts at once, with sound.
  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    closingRef.current = false
    if (!dialog.open) dialog.showModal()
    const unlock = lockScroll()
    const fade = dialog.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: reducedMotion() ? 160 : 240,
      easing: 'ease-out',
      fill: 'backwards',
    })
    const video = videoRef.current
    ;(video ?? closeRef.current)?.focus({ preventScroll: true })
    if (video) {
      // The click that opened the viewer allows sound. Should the browser still refuse, the
      // video waits, paused, for the presenter to press play.
      video.play().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'NotAllowedError') playRefused()
      })
    }

    const onCancel = (event: Event) => {
      // Escape: fade out like the close button. A browser may refuse to let a page hold the
      // dialog open on a repeated Escape; then "close" below tidies up.
      if (!event.cancelable) return
      event.preventDefault()
      escape()
    }
    const onClose = () => {
      // Also fired late for a close() made while React re-ran this effect: ignore it if open.
      if (!dialog.open) closedByBrowser()
    }
    const onFullscreen = () => fullscreenChanged()
    dialog.addEventListener('cancel', onCancel)
    dialog.addEventListener('close', onClose)
    document.addEventListener('fullscreenchange', onFullscreen)
    return () => {
      dialog.removeEventListener('cancel', onCancel)
      dialog.removeEventListener('close', onClose)
      document.removeEventListener('fullscreenchange', onFullscreen)
      fade.cancel()
      unlock()
      if (dialog.open) dialog.close()
    }
  }, [])

  const mediaProps = {
    className: 'media-viewer-media',
    src: media.url,
    'data-fit': ratio ? '' : undefined,
    style: ratio ? ({ '--ratio': ratio } as React.CSSProperties) : undefined,
    onError: () => {
      setFailed(true)
      setLoading(false)
    },
  }
  const waiting = loading && !failed

  return (
    <dialog
      ref={dialogRef}
      className="media-viewer"
      aria-labelledby={titleId}
      data-opening-dialog=""
      data-type={media.type}
      data-failed={failed ? '' : undefined}
      data-attention={failed || blocked ? '' : undefined}
      onClick={onClick}
    >
      <div className="media-viewer-stage">
        {/* The picture or video in its own box, so the link's arrow sits on it. */}
        <div className="media-viewer-frame">
          {media.type === 'image' ? (
            // eslint-disable-next-line @next/next/no-img-element -- re-encoded WebP from the API, shown at full size
            <img
              {...mediaProps}
              alt={title}
              decoding="async"
              onLoad={(event) => {
                const image = event.currentTarget
                fit(image.naturalWidth, image.naturalHeight)
                setLoading(false)
                appear(image)
              }}
            />
          ) : (
            <video
              ref={videoRef}
              {...mediaProps}
              controls
              autoPlay
              playsInline
              preload="auto"
              // The page is full screen already; the video's own full-screen button would only confuse.
              controlsList="nofullscreen"
              onLoadedMetadata={(event) => fit(event.currentTarget.videoWidth, event.currentTarget.videoHeight)}
              onCanPlay={() => setLoading(false)}
              onPlaying={() => {
                setLoading(false)
                setBlocked(false)
              }}
              onWaiting={() => setLoading(true)}
            />
          )}
          {link ? (
            <a
              href={link}
              data-opening-link=""
              title="Open the link"
              aria-label={`Open the link for ${title}${link.startsWith('/') ? '' : ' (opens in a new tab)'}`}
              className="media-viewer-link media-viewer-chrome"
              {...newTab(link)}
            >
              <ArrowUpRight aria-hidden strokeWidth={2.25} />
            </a>
          ) : null}
        </div>
        {waiting ? <span aria-hidden className="media-viewer-spinner" /> : null}
        {failed ? (
          <p role="alert" className="media-viewer-error">
            {media.type === 'video' ? 'This video could not be played.' : 'This picture could not be shown.'}
          </p>
        ) : null}
        {blocked && !failed && !waiting ? (
          <button
            type="button"
            className="media-viewer-play"
            aria-label="Play"
            onClick={() => void videoRef.current?.play().catch(() => {})}
          >
            <Play aria-hidden strokeWidth={2.25} />
          </button>
        ) : null}
        <p role="status" className="sr-only">
          {waiting ? 'Loading…' : ''}
        </p>
      </div>
      <p id={titleId} className="media-viewer-title media-viewer-chrome">
        {title}
      </p>
      <button
        ref={closeRef}
        type="button"
        className="opening-round media-viewer-close media-viewer-chrome"
        aria-label="Close"
        onClick={requestClose}
      >
        <X aria-hidden strokeWidth={2.25} />
      </button>
    </dialog>
  )
}
