'use client'

import { Search, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { countLabel, type PeopleList } from '@/lib/gallery'
import { Coin } from './coin'
import { goTo } from './route'
import { useFetched } from './use-fetched'

const PAGE = 60
const POLL_MS = 15_000
const SEARCH_WAIT_MS = 250

export function PeopleTab() {
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [pollMs, setPollMs] = useState(0)
  const { data, error, loading, reload } = useFetched<PeopleList>('/people', { q: query, limit }, pollMs)
  const sentinel = useRef<HTMLDivElement>(null)

  const pending = data?.pending ?? 0
  // While files are still being sorted, the list keeps itself up to date.
  const wanted = pending > 0 ? POLL_MS : 0
  if (wanted !== pollMs) setPollMs(wanted)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(typed.trim())
      setLimit(PAGE)
    }, SEARCH_WAIT_MS)
    return () => window.clearTimeout(timer)
  }, [typed])

  const shown = data?.people.length ?? 0
  const more = data ? shown < data.total : false
  useEffect(() => {
    const element = sentinel.current
    if (!element || !more) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setLimit((value) => value + PAGE)
      },
      { rootMargin: '600px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [more, shown])

  return (
    <div className="gallery-people">
      <div className="gallery-search">
        <Search aria-hidden />
        <input
          type="search"
          value={typed}
          placeholder="Search by name"
          aria-label="Search people by name"
          autoComplete="off"
          enterKeyHint="search"
          onChange={(event) => setTyped(event.target.value)}
        />
        {typed ? (
          <button type="button" aria-label="Clear the search" onClick={() => setTyped('')}>
            <X aria-hidden />
          </button>
        ) : null}
      </div>

      {pending > 0 ? (
        <p className="gallery-note" role="status">
          {pending.toLocaleString('en-IN')} new {pending === 1 ? 'file is' : 'files are'} still being sorted. This list updates by itself.
        </p>
      ) : null}

      {error && !data ? (
        <div className="gallery-empty" role="alert">
          <p>{error}</p>
          <button type="button" className="gallery-ghost" onClick={reload}>
            Try again
          </button>
        </div>
      ) : null}

      {!data && !error ? <SkeletonCoins /> : null}

      {data && data.people.length === 0 ? (
        <div className="gallery-empty">
          {query ? (
            <p>Nobody here is called “{query}” yet. They appear once they add a selfie, or you can try part of the name.</p>
          ) : (
            <>
              <p>Nobody here yet. Share your photos, and faces appear here as they are sorted.</p>
              <button type="button" className="gallery-ghost" onClick={() => goTo({ tab: 'upload', person: null, item: null })}>
                Upload photos
              </button>
            </>
          )}
        </div>
      ) : null}

      {data && data.people.length > 0 ? (
        <ul className="gallery-coins" aria-busy={loading || undefined}>
          {data.people.map((person) => {
            const count = person.photos + person.videos
            const label = person.name || 'Untitled'
            return (
              <li key={person.id}>
                <button type="button" className="gallery-person" onClick={() => goTo({ tab: 'people', person: person.id, item: null })}>
                  <Coin src={person.avatar_url} />
                  <span className="gallery-person-name" data-untitled={person.name ? undefined : ''} title={label}>
                    {person.is_me ? 'You' : label}
                  </span>
                  <span className="gallery-person-count">
                    <span aria-hidden>{count.toLocaleString('en-IN')}</span>
                    <span className="sr-only">{countLabel(person.photos, person.videos)}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      ) : null}
      {more ? <div ref={sentinel} aria-hidden className="gallery-sentinel" /> : null}
    </div>
  )
}

function SkeletonCoins() {
  return (
    <ul className="gallery-coins" aria-hidden>
      {Array.from({ length: 12 }, (_, index) => (
        <li key={index} className="gallery-person">
          <span className="gallery-skeleton-coin" />
          <span className="gallery-skeleton-line" />
        </li>
      ))}
    </ul>
  )
}
