'use client'

import { useEffect, useState, useSyncExternalStore } from 'react'
import { adminApi, ApiError, setCsrfToken, type RequestOptions } from '@/lib/api'
import { serverClock } from '@/lib/time'
import { goToLogin } from './errors'
import type { AdminMe, AssetKind, UploadedAsset } from './types'

type Query = RequestOptions['query']

export type QuerySnapshot<T> = {
  data: T | undefined
  /** Error of the latest request for the current target (cleared by the next success). */
  error: ApiError | null
  /** Target key that produced `data`. Differs from the current key while a new filter loads. */
  dataKey: string | null
  /** A request is in flight. */
  pending: boolean
  /** Device time of the last successful load. */
  loadedAt: number | null
}

/**
 * One authoritative snapshot fetched over HTTP. Lives outside React so effects only
 * "talk to an external system", and so refetches can be deduplicated and throttled:
 *
 * - `reload()` while a request is in flight queues exactly one follow-up request;
 * - `reloadSoon(ms)` coalesces bursts (vote tallies) into at most one request per `ms`.
 */
class Resource<T> {
  private snap: QuerySnapshot<T> = { data: undefined, error: null, dataKey: null, pending: false, loadedAt: null }
  private readonly listeners = new Set<() => void>()
  private path: string | null = null
  private query: Query = undefined
  private key: string | null = null
  private controller: AbortController | null = null
  private inflight: Promise<void> | null = null
  private again = false
  private lastStart = 0
  private soonTimer: ReturnType<typeof setTimeout> | undefined
  private attached = false

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getSnapshot = () => this.snap

  private set(patch: Partial<QuerySnapshot<T>>) {
    this.snap = { ...this.snap, ...patch }
    for (const listener of this.listeners) listener()
  }

  attach(path: string | null, query: Query, key: string | null) {
    this.attached = true
    this.path = path
    this.query = query
    this.key = key
    this.controller?.abort()
    this.controller = null
    this.inflight = null
    this.again = false
    if (path) void this.run()
  }

  detach() {
    this.attached = false
    this.controller?.abort()
    this.controller = null
    this.inflight = null
    this.again = false
    if (this.soonTimer !== undefined) clearTimeout(this.soonTimer)
    this.soonTimer = undefined
  }

  reload = (): Promise<void> => {
    if (!this.attached || !this.path) return Promise.resolve()
    if (this.inflight) {
      this.again = true
      return this.inflight.then(() => this.inflight ?? undefined)
    }
    return this.run()
  }

  reloadSoon = (minGapMs = 1500) => {
    if (!this.attached || this.soonTimer !== undefined) return
    const wait = Math.max(0, this.lastStart + minGapMs - Date.now())
    this.soonTimer = setTimeout(() => {
      this.soonTimer = undefined
      void this.reload()
    }, wait)
  }

  /** Local edit of the cached snapshot (the next fetch replaces it). */
  mutate = (update: (data: T) => T) => {
    if (this.snap.data !== undefined) this.set({ data: update(this.snap.data) })
  }

  private run(): Promise<void> {
    const promise: Promise<void> = this.fetchOnce().finally(() => {
      if (this.inflight === promise) this.inflight = null
      if (this.again && this.attached) {
        this.again = false
        void this.run()
      }
    })
    this.inflight = promise
    return promise
  }

  private async fetchOnce(): Promise<void> {
    const path = this.path
    if (!path) return
    const key = this.key
    const controller = new AbortController()
    this.controller = controller
    const started = Date.now()
    this.lastStart = started
    this.set({ pending: true })
    try {
      const data = await adminApi<T>(path, { query: this.query, signal: controller.signal })
      if (controller.signal.aborted || key !== this.key) return
      const ended = Date.now()
      const serverTime = (data as { server_time?: unknown } | null)?.server_time
      if (typeof serverTime === 'string') serverClock.observe(serverTime, started, ended)
      this.set({ data, error: null, dataKey: key, pending: false, loadedAt: ended })
    } catch (error) {
      if (controller.signal.aborted || key !== this.key) return
      if (error instanceof ApiError && error.status === 401) {
        goToLogin()
        return
      }
      const apiError = error instanceof ApiError ? error : new ApiError(0, 'NETWORK', String(error))
      this.set({ error: apiError, pending: false })
    } finally {
      if (this.controller === controller) this.controller = null
    }
  }
}

export type AdminQuery<T> = QuerySnapshot<T> & {
  /** First load has not finished yet. */
  loading: boolean
  /** `data` belongs to a previous target (filter or page) while the new one loads. */
  stale: boolean
  reload: () => Promise<void>
  reloadSoon: (minGapMs?: number) => void
  mutate: (update: (data: T) => T) => void
}

/** Fetch `GET /api/v1{path}` and keep it fresh on demand. `path = null` pauses. */
export function useAdminQuery<T>(path: string | null, query?: Query): AdminQuery<T> {
  const [resource] = useState(() => new Resource<T>())
  const snap = useSyncExternalStore(resource.subscribe, resource.getSnapshot, resource.getSnapshot)
  const queryJson = JSON.stringify(query ?? {})
  const key = path ? `${path}?${queryJson}` : null
  useEffect(() => {
    resource.attach(path, JSON.parse(queryJson) as Query, path ? `${path}?${queryJson}` : null)
    return () => resource.detach()
  }, [resource, path, queryJson])
  return {
    ...snap,
    loading: snap.data === undefined && !snap.error,
    stale: snap.data !== undefined && snap.dataKey !== key,
    reload: resource.reload,
    reloadSoon: resource.reloadSoon,
    mutate: resource.mutate,
  }
}

/** Refresh the in-memory CSRF token from the session. */
export async function refreshCsrf(): Promise<AdminMe> {
  const me = await adminApi<AdminMe>('/admin/me')
  setCsrfToken('admin', me.csrf_token)
  return me
}

/**
 * State-changing admin request. A 401 sends the organiser to sign-in; a CSRF failure
 * (token lost after a reload or a new session) refreshes the token and retries once —
 * the server rejected the first request before doing anything.
 */
export async function adminAction<T = { ok: boolean }>(path: string, options: RequestOptions = {}): Promise<T> {
  const run = () => adminApi<T>(path, { method: 'POST', ...options })
  try {
    return await run()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      goToLogin()
      throw error
    }
    if (error instanceof ApiError && error.code === 'CSRF_FAILED') {
      await refreshCsrf()
      return run()
    }
    throw error
  }
}

export function uploadAsset(kind: AssetKind, file: File): Promise<UploadedAsset> {
  const form = new FormData()
  form.set('kind', kind)
  form.set('file', file)
  return adminAction<UploadedAsset>('/admin/assets', { form, timeoutMs: 60_000 })
}
