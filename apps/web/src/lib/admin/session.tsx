'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { adminApi, ApiError, setCsrfToken } from '@/lib/api'
import { useLiveChannel, type LiveSignal, type LiveStatus } from '@/lib/live'
import { goToLogin, isUnauthenticated } from './errors'
import type { AdminMe } from './types'

type SignalListener = (signal: LiveSignal) => void

export type AdminSession = {
  me: AdminMe
  /** Re-read the session (programme name, timezone, CSRF token). */
  refreshMe: () => Promise<void>
  connection: LiveStatus
  subscribe: (listener: SignalListener) => () => void
}

const AdminSessionContext = createContext<AdminSession | null>(null)

type LoadState = { me: AdminMe | null; error: ApiError | null }

/**
 * Loads the organiser session once, keeps the CSRF token in memory, and owns the single
 * admin WebSocket. Pages subscribe to its "something changed" signals and refetch.
 */
export function AdminSessionProvider({
  children,
  renderPending,
}: {
  children: React.ReactNode
  renderPending: (state: { error: ApiError | null; retry: () => void }) => React.ReactNode
}) {
  const [state, setState] = useState<LoadState>({ me: null, error: null })
  const [listeners] = useState(() => new Set<SignalListener>())

  const load = useCallback(
    () =>
      adminApi<AdminMe>('/admin/me').then(
        (me) => {
          setCsrfToken('admin', me.csrf_token)
          setState({ me, error: null })
        },
        (error: unknown) => {
          if (isUnauthenticated(error)) {
            goToLogin()
            return
          }
          setState((prev) => ({
            me: prev.me,
            error: error instanceof ApiError ? error : new ApiError(0, 'NETWORK', String(error)),
          }))
        },
      ),
    [],
  )

  useEffect(() => {
    void load()
  }, [load])

  const onSignal = useCallback(
    (signal: LiveSignal) => {
      if (signal.type === 'revoked') {
        goToLogin()
        return
      }
      if (signal.type === 'hello' || signal.type === 'resync') void load()
      for (const listener of listeners) listener(signal)
    },
    [listeners, load],
  )

  const connection = useLiveChannel('/ws/admin', onSignal, state.me !== null)

  const subscribe = useCallback(
    (listener: SignalListener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    [listeners],
  )

  const value = useMemo<AdminSession | null>(
    () => (state.me ? { me: state.me, refreshMe: load, connection, subscribe } : null),
    [state.me, load, connection, subscribe],
  )

  if (!value) return <>{renderPending({ error: state.error, retry: () => void load() })}</>
  return <AdminSessionContext.Provider value={value}>{children}</AdminSessionContext.Provider>
}

export function useAdminSession(): AdminSession {
  const session = useContext(AdminSessionContext)
  if (!session) throw new Error('useAdminSession must be used inside the admin console')
  return session
}

/** Run `handler` for every live-channel signal while the component is mounted. */
export function useAdminSignal(handler: SignalListener) {
  const { subscribe } = useAdminSession()
  const latest = useRef(handler)
  useEffect(() => {
    latest.current = handler
  })
  useEffect(() => subscribe((signal) => latest.current(signal)), [subscribe])
}
