'use client'

import { ChevronLeft, ChevronRight, Download, LoaderCircle, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { downloadName, type GalleryItem } from '@/lib/gallery'

const SWIPE_PX = 50

/** One photo or video on black, as it was shared, with no marks on it. Swipe or use the arrows. */
export function Viewer({
  items,
  index,
  total,
  onClose,
  onMove,
}: {
  items: GalleryItem[]
  index: number
  total: number
  onClose: () => void
  onMove: (index: number) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const swipe = useRef<{ x: number; y: number } | null>(null)
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [failedId, setFailedId] = useState<string | null>(null)
  const item = items[index]
  const previous = index > 0 ? index - 1 : null
  const next = index < items.length - 1 ? index + 1 : null

  useEffect(() => {
    const element = dialog.current
    if (element && !element.open) element.showModal()
    return () => {
      if (element?.open) element.close()
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft' && previous !== null) onMove(previous)
      if (event.key === 'ArrowRight' && next !== null) onMove(next)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [previous, next, onMove])

  // The next photo is fetched ahead, so swiping feels immediate.
  const upcoming = next !== null ? items[next] : undefined
  useEffect(() => {
    if (upcoming?.type === 'image') new Image().src = upcoming.url
  }, [upcoming])

  if (!item) return null
  const loading = item.type === 'image' && loadedId !== item.id && failedId !== item.id

  return (
    <dialog
      ref={dialog}
      className="gallery-viewer"
      aria-label={item.name || 'Photo'}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <div
        className="gallery-viewer-stage"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose()
        }}
        onTouchStart={(event) => {
          const touch = event.touches[0]
          swipe.current = event.touches.length === 1 && touch ? { x: touch.clientX, y: touch.clientY } : null
        }}
        onTouchEnd={(event) => {
          const start = swipe.current
          const touch = event.changedTouches[0]
          swipe.current = null
          if (!start || !touch) return
          const dx = touch.clientX - start.x
          if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < Math.abs(touch.clientY - start.y)) return
          const target = dx < 0 ? next : previous
          if (target !== null) onMove(target)
        }}
      >
        {item.type === 'image' ? (
          // eslint-disable-next-line @next/next/no-img-element -- the file as it was shared
          <img
            key={item.id}
            src={item.url}
            alt={item.name}
            // The small picture holds the place until the full one arrives.
            style={{ backgroundImage: `url(${item.thumb_url})` }}
            onLoad={() => setLoadedId(item.id)}
            onError={() => setFailedId(item.id)}
          />
        ) : (
          <video key={item.id} src={item.url} poster={item.thumb_url} controls autoPlay playsInline preload="metadata" onError={() => setFailedId(item.id)} />
        )}
        {loading ? <LoaderCircle aria-hidden className="gallery-viewer-spinner animate-spin" /> : null}
        {failedId === item.id ? (
          <p role="alert" className="gallery-viewer-error">
            This {item.type === 'video' ? 'video could not be played here. Download it to watch it.' : 'photo could not be shown here. Download it to see it.'}
          </p>
        ) : null}
      </div>

      <div className="gallery-viewer-top">
        <button type="button" className="gallery-round" aria-label="Close" onClick={onClose}>
          <X aria-hidden />
        </button>
        <p className="gallery-viewer-count">
          {index + 1} of {total}
        </p>
        <a className="gallery-round" href={item.download_url} download={downloadName(item)} aria-label="Download the full-quality file">
          <Download aria-hidden />
        </a>
      </div>

      {previous !== null ? (
        <button type="button" className="gallery-round gallery-viewer-arrow" data-side="left" aria-label="Previous" onClick={() => onMove(previous)}>
          <ChevronLeft aria-hidden />
        </button>
      ) : null}
      {next !== null ? (
        <button type="button" className="gallery-round gallery-viewer-arrow" data-side="right" aria-label="Next" onClick={() => onMove(next)}>
          <ChevronRight aria-hidden />
        </button>
      ) : null}
    </dialog>
  )
}
