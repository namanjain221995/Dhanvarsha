'use client'

import { useSyncExternalStore } from 'react'

/**
 * Where the gallery page is, kept in the address so the phone's back button walks back through it
 * and a person's page can be shared: ?tab=people&person=<id>&item=<id>.
 */

export type GalleryRoute = { tab: 'upload' | 'people'; person: string | null; item: string | null }

const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  window.addEventListener('popstate', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('popstate', listener)
  }
}

function urlFor(route: GalleryRoute): string {
  const params = new URLSearchParams()
  if (route.tab === 'people') params.set('tab', 'people')
  if (route.person) params.set('person', route.person)
  if (route.item) params.set('item', route.item)
  const query = params.toString()
  return `${window.location.pathname}${query ? `?${query}` : ''}`
}

function parse(search: string): GalleryRoute {
  const params = new URLSearchParams(search)
  const person = params.get('person')
  return { tab: person || params.get('tab') === 'people' ? 'people' : 'upload', person, item: person ? params.get('item') : null }
}

export function useGalleryRoute(): GalleryRoute {
  const search = useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => '',
  )
  return parse(search)
}

/** Go somewhere new. Steps the page took itself are marked, so closing can go back through them. */
export function goTo(route: Partial<GalleryRoute>) {
  const next = { ...parse(window.location.search), ...route }
  const url = urlFor(next)
  if (url === window.location.pathname + window.location.search) return
  window.history.pushState({ svGallery: true }, '', url)
  listeners.forEach((listener) => listener())
}

/** Close the top layer: a real step back if this page opened it, else the same place without it. */
export function goBack(fallback: Partial<GalleryRoute>) {
  const state = window.history.state as { svGallery?: boolean } | null
  if (state?.svGallery) {
    window.history.back()
    return
  }
  const next = { ...parse(window.location.search), ...fallback }
  window.history.replaceState(null, '', urlFor(next))
  listeners.forEach((listener) => listener())
}

/** Moving between photos replaces the address, so one back press closes the viewer. */
export function showItem(item: string) {
  const next = { ...parse(window.location.search), item }
  window.history.replaceState(window.history.state, '', urlFor(next))
  listeners.forEach((listener) => listener())
}
