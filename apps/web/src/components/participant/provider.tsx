'use client'

import { useRouter } from 'next/navigation'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, participantApi, setCsrfToken } from '@/lib/api'
import { type LiveSignal, type LiveStatus, useLiveChannel } from '@/lib/live'
import { serverClock } from '@/lib/time'
import { clearVotingStorage } from './use-voting'
import type { Snapshot } from '@/lib/types'

/**
 * Holds the authoritative participant snapshot. The server is the only source of truth:
 * live signals and timers only trigger refetches, and voting controls are disabled
 * whenever the current state cannot be confirmed.
 */

type Status = 'loading' | 'ready' | 'signed_out' | 'error'

type ContextValue = {
  status: Status
  snapshot: Snapshot | null
  signedOutCode: string | null
  signedOutMessage: string | null
  connection: LiveStatus
  /** False while the latest state cannot be confirmed with the server. */
  authoritative: boolean
  stale: boolean
  lastRefreshAt: number | null
  refresh: (reason?: string) => Promise<Snapshot | null>
  signOut: () => Promise<void>
  /** Increments on every successful snapshot; detail pages refetch when it changes. */
  revision: number
}

const ParticipantContext = createContext<ContextValue | null>(null)

const AUTHORITATIVE_WINDOW_MS = 25_000

export function ParticipantProvider({ children }: { children: React.ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [status, setStatus] = useState<Status>('loading')
  const [signedOut, setSignedOut] = useState<{ code: string; message: string } | null>(null)
  const [stale, setStale] = useState(false)
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null)
  const [revision, setRevision] = useState(0)
  const [nowTick, setNowTick] = useState(() => Date.now())
  const router = useRouter()
  const inflight = useRef<Promise<Snapshot | null> | null>(null)
  const again = useRef(false)
  const versionRef = useRef<number>(-1)
  const jitterTimer = useRef<number | undefined>(undefined)
  const stopped = useRef(false)

  const doFetch = useCallback(async (): Promise<Snapshot | null> => {
    const started = Date.now()
    try {
      const data = await participantApi<Snapshot>('/me/snapshot', { timeoutMs: 10_000 })
      serverClock.observe(data.server_time, started, Date.now())
      setCsrfToken('participant', data.csrf_token)
      versionRef.current = data.state_version
      setSnapshot(data)
      setStatus('ready')
      setStale(false)
      setLastRefreshAt(Date.now())
      setRevision((r) => r + 1)
      return data
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.code === 'ACCOUNT_DISABLED')) {
        stopped.current = true
        clearVotingStorage()
        setSignedOut({ code: error.code, message: error.message })
        setStatus('signed_out')
        setCsrfToken('participant', null)
        return null
      }
      setStale(true)
      setStatus((s) => (s === 'loading' ? 'error' : s))
      return null
    }
  }, [])

  const refresh = useCallback(async (): Promise<Snapshot | null> => {
    if (stopped.current) return null
    if (inflight.current) {
      again.current = true
      return inflight.current
    }
    const run = (async () => {
      let result = await doFetch()
      while (again.current && !stopped.current) {
        again.current = false
        result = await doFetch()
      }
      return result
    })()
    inflight.current = run
    try {
      return await run
    } finally {
      inflight.current = null
    }
  }, [doFetch])

  const onSignal = useCallback(
    (signal: LiveSignal) => {
      if (stopped.current) return
      switch (signal.type) {
        case 'state': {
          if (typeof signal.v === 'number' && signal.v <= versionRef.current) return
          // Spread a full venue's refetches over a short window after a broadcast.
          window.clearTimeout(jitterTimer.current)
          jitterTimer.current = window.setTimeout(() => void refresh(), Math.random() * 700)
          return
        }
        case 'hello': {
          // A new or restored connection: re-check session, round and receipts, because
          // changes may have been missed while disconnected (and the client may be stale).
          window.clearTimeout(jitterTimer.current)
          jitterTimer.current = window.setTimeout(() => void refresh(), Math.random() * 1000)
          return
        }
        case 'resync':
        case 'poll':
        case 'visible':
          void refresh()
          return
        case 'revoked':
          void refresh()
          return
        default:
          return
      }
    },
    [refresh],
  )

  const connection = useLiveChannel('/ws', onSignal, status !== 'signed_out')

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => setNowTick(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [refresh])

  // Safety net: even with a healthy socket, confirm state once a minute.
  useEffect(() => {
    const id = window.setInterval(() => void refresh(), 60_000)
    return () => window.clearInterval(id)
  }, [refresh])

  const signOut = useCallback(async () => {
    try {
      await participantApi('/auth/logout', { method: 'POST' })
    } finally {
      stopped.current = true
      clearVotingStorage()
      setCsrfToken('participant', null)
      router.replace('/?notice=signed_out')
    }
  }, [router])

  const authoritative = useMemo(() => {
    if (stale || !snapshot || status !== 'ready') return false
    if (typeof navigator !== 'undefined' && !navigator.onLine) return false
    if (connection === 'live') return true
    return lastRefreshAt !== null && Math.max(nowTick, lastRefreshAt) - lastRefreshAt < AUTHORITATIVE_WINDOW_MS
  }, [stale, snapshot, status, connection, lastRefreshAt, nowTick])

  const value: ContextValue = {
    status,
    snapshot,
    signedOutCode: signedOut?.code ?? null,
    signedOutMessage: signedOut?.message ?? null,
    connection,
    authoritative,
    stale,
    lastRefreshAt,
    refresh,
    signOut,
    revision,
  }
  return <ParticipantContext.Provider value={value}>{children}</ParticipantContext.Provider>
}

export function useParticipant(): ContextValue {
  const value = useContext(ParticipantContext)
  if (!value) throw new Error('useParticipant must be used inside ParticipantProvider')
  return value
}
