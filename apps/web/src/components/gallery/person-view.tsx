'use client'

import { ArrowLeft, Play } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { countLabel, formatDuration, type GalleryItem, type PersonPage } from '@/lib/gallery'
import { Coin } from './coin'
import { goBack, goTo, showItem } from './route'
import { useFetched } from './use-fetched'
import { Viewer } from './viewer'

const PAGE = 60

/** A person's page: everything they appear in, each photo in its own shape, nothing cropped away. */
export function PersonView({ personId, itemId, onManage }: { personId: string; itemId: string | null; onManage: () => void }) {
  const [limit, setLimit] = useState(PAGE)
  const { data, error, reload } = useFetched<PersonPage>(`/people/${encodeURIComponent(personId)}`, { limit })
  const sentinel = useRef<HTMLDivElement>(null)
  const shown = data?.media.length ?? 0
  const more = data ? shown < data.total : false

  useEffect(() => {
    const element = sentinel.current
    if (!element || !more) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setLimit((value) => value + PAGE)
      },
      { rootMargin: '800px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [more, shown])

  const close = () => goBack({ person: null, item: null, tab: 'people' })
  const open = (item: GalleryItem) => goTo({ item: item.id })

  const person = data?.person
  const name = person ? person.name || 'Untitled' : ''
  const index = data && itemId ? data.media.findIndex((item) => item.id === itemId) : -1

  return (
    <div className="gallery-person-page">
      <button type="button" className="gallery-back" onClick={close}>
        <ArrowLeft aria-hidden />
        People
      </button>

      {error && !data ? (
        <div className="gallery-empty" role="alert">
          <p>{error}</p>
          <button type="button" className="gallery-ghost" onClick={reload}>
            Try again
          </button>
        </div>
      ) : null}

      {person ? (
        <header className="gallery-person-head">
          <Coin src={person.avatar_url} size="large" />
          <div>
            <h2 data-untitled={person.name ? undefined : ''}>{name}</h2>
            <p>{countLabel(person.photos, person.videos)}</p>
            {person.is_me ? (
              <button type="button" className="gallery-link" onClick={onManage}>
                Change my selfie or name
              </button>
            ) : null}
          </div>
        </header>
      ) : null}

      {!data && !error ? <div className="gallery-skeleton-photos" aria-hidden /> : null}

      {data && data.media.length === 0 ? (
        <div className="gallery-empty">
          {person?.is_me ? (
            <>
              <p>No photos of you yet. They appear here as people share theirs, and yours count too.</p>
              <button type="button" className="gallery-ghost" onClick={() => goTo({ tab: 'upload', person: null, item: null })}>
                Upload photos
              </button>
            </>
          ) : (
            <p>No photos of {name} yet. They appear here as people share theirs.</p>
          )}
        </div>
      ) : null}

      {data && data.media.length > 0 ? (
        <ul className="gallery-photos">
          {data.media.map((item) => {
            const ratio = item.width > 0 && item.height > 0 ? Math.min(2.4, Math.max(0.5, item.width / item.height)) : 1
            return (
              <li key={item.id} className="gallery-photo" style={{ '--ratio': ratio } as React.CSSProperties}>
                <button
                  type="button"
                  aria-label={`${item.type === 'video' ? 'Play the video' : 'Open the photo'} ${item.name}`}
                  onClick={() => open(item)}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- a small WebP from the API */}
                  <img src={item.thumb_url} alt="" loading="lazy" decoding="async" />
                  {item.type === 'video' ? (
                    <span className="gallery-photo-video">
                      <Play aria-hidden />
                      {item.duration_ms > 0 ? formatDuration(item.duration_ms) : null}
                    </span>
                  ) : null}
                </button>
              </li>
            )
          })}
        </ul>
      ) : null}
      {more ? <div ref={sentinel} aria-hidden className="gallery-sentinel" /> : null}

      {data && index >= 0 ? (
        <Viewer
          items={data.media}
          index={index}
          total={data.total}
          onClose={() => goBack({ item: null })}
          onMove={(next) => {
            const item = data.media[next]
            if (item) showItem(item.id)
          }}
        />
      ) : null}
    </div>
  )
}
