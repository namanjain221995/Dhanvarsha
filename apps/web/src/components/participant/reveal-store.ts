'use client'

import { useSyncExternalStore } from 'react'
import type { PublicOpening } from '@/lib/types'

/*
 * Which opening cards this browser has scratched open, kept in localStorage so a projector
 * that reloads keeps its reveals. The organiser's "Hide all cards again" raises the server's
 * reveal epoch; reveals saved under another epoch no longer count.
 *
 * Storage can be missing or refuse writes (private windows, blocked site data), so every
 * access is wrapped and an in-memory copy carries on for this tab. Other tabs on the same
 * screen follow through the storage event.
 */

const KEY = 'sv:opening-reveals'
/** More than any page can hold (the API allows 40 cards); guards against a hand-edited value. */
const MAX_IDS = 200

export type StoredReveals = { epoch: number | null; ids: readonly string[] }

const EMPTY: StoredReveals = { epoch: null, ids: [] }

let current: StoredReveals | null = null
const listeners = new Set<() => void>()

function parse(raw: string | null): StoredReveals {
  if (!raw) return EMPTY
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return EMPTY
    const { epoch, ids } = value as { epoch?: unknown; ids?: unknown }
    if (typeof epoch !== 'number' || !Number.isInteger(epoch) || !Array.isArray(ids)) return EMPTY
    return { epoch, ids: ids.filter((id): id is string => typeof id === 'string').slice(0, MAX_IDS) }
  } catch {
    return EMPTY
  }
}

function snapshot(): StoredReveals {
  if (current) return current
  let raw: string | null = null
  try {
    raw = window.localStorage.getItem(KEY)
  } catch {
    raw = null
  }
  current = parse(raw)
  return current
}

function serverSnapshot(): StoredReveals {
  return EMPTY
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const onStorage = (event: StorageEvent) => {
    // A null key means another tab cleared the whole storage.
    if (event.key !== KEY && event.key !== null) return
    current = parse(event.key === null ? null : event.newValue)
    listener()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

export function saveReveals(next: StoredReveals) {
  current = next
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ epoch: next.epoch, ids: next.ids }))
  } catch {
    // Storage refused: the reveals last as long as this tab.
  }
  for (const listener of [...listeners]) listener()
}

/** The saved value right now, for event handlers (render reads it with useStoredReveals). */
export const readReveals = snapshot

/** What this browser saved. Empty on the server and during hydration, so both render alike. */
export function useStoredReveals(): StoredReveals {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot)
}

/** The revealed cards that still count: saved under the current epoch, and still on the page. */
export function liveReveals(stored: StoredReveals, opening: PublicOpening): ReadonlySet<string> {
  if (!opening.hidden || stored.epoch !== opening.reveal_epoch) return new Set()
  const onPage = new Set(opening.cards.map((card) => card.id))
  return new Set(stored.ids.filter((id) => onPage.has(id)))
}

/** The stored value with what no longer counts dropped, or null when nothing needs to change. */
export function prunedReveals(stored: StoredReveals, opening: PublicOpening): StoredReveals | null {
  if (!opening.hidden || stored.ids.length === 0) return null
  const live = liveReveals(stored, opening)
  if (stored.epoch === opening.reveal_epoch && live.size === stored.ids.length) return null
  return { epoch: opening.reveal_epoch, ids: [...live] }
}

/** The stored value with one more card revealed under the current epoch. */
export function withReveal(stored: StoredReveals, opening: PublicOpening, id: string): StoredReveals {
  const live = liveReveals(stored, opening)
  return { epoch: opening.reveal_epoch, ids: live.has(id) ? [...live] : [...live, id] }
}
