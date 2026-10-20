'use client'

import { useCallback, useEffect, useReducer, useRef } from 'react'
import { ApiError, newIdempotencyKey, participantApi } from '@/lib/api'
import type { EventDetail, Receipt } from '@/lib/types'

/**
 * Per-ballot submission state machine.
 *
 *   idle -> submitting -> recorded            (server returned a committed receipt)
 *                      -> error               (definite refusal, e.g. voting closed)
 *                      -> checking -> ...     (no answer: network loss, timeout, 5xx)
 *
 * "checking" asks the server for a receipt and, only if there is none, retries with the
 * SAME idempotency key. It never shows failure or offers another choice until the first
 * attempt is reconciled. Pending attempts survive reloads in sessionStorage. A selection
 * that was never submitted is only a draft: it is never sent automatically.
 */

export type SubmitPhase =
  | { phase: 'idle' }
  | { phase: 'submitting'; nomineeId: string; key: string }
  | { phase: 'checking'; nomineeId: string; key: string; attempt: number }
  | { phase: 'error'; code: string; message: string }

export type BallotState = {
  draft: string | null
  submit: SubmitPhase
  receipt: Receipt | null
  celebrate: boolean
  note: string | null
}

type State = Record<string, BallotState>

type Action =
  | { type: 'sync'; detail: EventDetail; drafts: Record<string, string | null> }
  | { type: 'select'; ballotId: string; nomineeId: string }
  | { type: 'submit'; ballotId: string; phase: SubmitPhase }
  | { type: 'recorded'; ballotId: string; receipt: Receipt; celebrate: boolean; note?: string | null }

const empty: BallotState = { draft: null, submit: { phase: 'idle' }, receipt: null, celebrate: false, note: null }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'sync': {
      const next: State = {}
      for (const ballot of action.detail.ballots) {
        const prev = state[ballot.id] ?? empty
        const nomineeIds = new Set(ballot.nominees.map((n) => n.id))
        const restored = action.drafts[ballot.id]
        const draft = prev.draft ?? (restored && nomineeIds.has(restored) ? restored : null)
        next[ballot.id] = {
          ...prev,
          draft: draft && nomineeIds.has(draft) ? draft : null,
          // The server's receipt always wins over local state.
          receipt: ballot.receipt ?? prev.receipt,
          submit: ballot.receipt ? { phase: 'idle' } : prev.submit,
        }
      }
      return next
    }
    case 'select': {
      const prev = state[action.ballotId] ?? empty
      if (prev.receipt || prev.submit.phase === 'submitting' || prev.submit.phase === 'checking') return state
      return { ...state, [action.ballotId]: { ...prev, draft: action.nomineeId, submit: { phase: 'idle' } } }
    }
    case 'submit': {
      const prev = state[action.ballotId] ?? empty
      return { ...state, [action.ballotId]: { ...prev, submit: action.phase } }
    }
    case 'recorded': {
      const prev = state[action.ballotId] ?? empty
      return {
        ...state,
        [action.ballotId]: {
          ...prev,
          receipt: action.receipt,
          submit: { phase: 'idle' },
          celebrate: action.celebrate,
          note: action.note ?? null,
          draft: null,
        },
      }
    }
  }
}

// Drafts and pending attempts belong to one participant: a shared phone or help-desk
// laptop must never restore or resend someone else's choice.
const draftKey = (owner: string, ballotId: string) => `sv:${owner}:draft:${ballotId}`
const pendingKey = (owner: string, ballotId: string) => `sv:${owner}:pending:${ballotId}`

function storage(owner: string | null): Storage | null {
  if (!owner) return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/** Forget every stored draft and pending attempt (sign-in, sign-out, session ended). */
export function clearVotingStorage() {
  try {
    const store = window.sessionStorage
    for (const key of Object.keys(store)) if (key.startsWith('sv:')) store.removeItem(key)
  } catch {
    /* storage unavailable: nothing to clear */
  }
}

function readPending(owner: string | null, ballotId: string): { key: string; nomineeId: string } | null {
  if (!owner) return null
  try {
    const raw = storage(owner)?.getItem(pendingKey(owner, ballotId))
    return raw ? (JSON.parse(raw) as { key: string; nomineeId: string }) : null
  } catch {
    return null
  }
}

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

function waitForOnline(): Promise<void> {
  if (navigator.onLine) return Promise.resolve()
  return new Promise((resolve) => window.addEventListener('online', () => resolve(), { once: true }))
}

const DEFINITE_REFUSALS: Record<string, string> = {
  VOTING_CLOSED: 'Voting for this round closed before your vote arrived. It was not recorded.',
  VOTING_NOT_OPEN: 'This round is not open for voting yet.',
  PROGRAMME_ACCESS_CLOSED: 'The programme is not open for voting now.',
  INVALID_NOMINEE: 'That choice is no longer available. Review the list and choose again.',
}

export function useVoting(detail: EventDetail | null, participantId: string | null, onRecorded: () => void) {
  const [state, dispatch] = useReducer(reducer, {})
  const mounted = useRef(true)
  const running = useRef(new Set<string>())
  const onRecordedRef = useRef(onRecorded)
  useEffect(() => {
    onRecordedRef.current = onRecorded
  }, [onRecorded])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const record = useCallback((ballotId: string, receipt: Receipt, celebrate: boolean, note?: string) => {
    storage(participantId)?.removeItem(pendingKey(participantId ?? '', ballotId))
    storage(participantId)?.removeItem(draftKey(participantId ?? '', ballotId))
    if (mounted.current) dispatch({ type: 'recorded', ballotId, receipt, celebrate, note })
    onRecordedRef.current()
  }, [participantId])

  const fail = useCallback((ballotId: string, code: string, message: string) => {
    storage(participantId)?.removeItem(pendingKey(participantId ?? '', ballotId))
    if (mounted.current) dispatch({ type: 'submit', ballotId, phase: { phase: 'error', code, message } })
  }, [participantId])

  /** Resolve an attempt whose outcome is unknown. Only ever retries with the same key. */
  const reconcile = useCallback(
    async (ballotId: string, nomineeId: string, key: string) => {
      if (running.current.has(ballotId)) return
      running.current.add(ballotId)
      try {
        for (let attempt = 1; mounted.current; attempt += 1) {
          dispatch({ type: 'submit', ballotId, phase: { phase: 'checking', nomineeId, key, attempt } })
          await waitForOnline()
          try {
            const found = await participantApi<{ receipt: Receipt }>(`/me/ballots/${ballotId}/receipt`, { timeoutMs: 8000 })
            record(ballotId, found.receipt, true)
            return
          } catch (error) {
            if (!(error instanceof ApiError)) throw error
            if (error.code === 'NO_VOTE') {
              // Nothing was stored: resend the same request (same key, same choice).
              try {
                const result = await participantApi<{ receipt: Receipt }>(`/me/ballots/${ballotId}/vote`, {
                  method: 'POST',
                  body: { nominee_id: nomineeId },
                  idempotencyKey: key,
                  timeoutMs: 12_000,
                })
                record(ballotId, result.receipt, true)
                return
              } catch (retryError) {
                if (!(retryError instanceof ApiError)) throw retryError
                if (!retryError.isNetwork && retryError.status < 500) {
                  const receipt = retryError.details.receipt as Receipt | undefined
                  if (receipt) {
                    record(ballotId, receipt, false, 'Your vote was already recorded.')
                    return
                  }
                  fail(ballotId, retryError.code, DEFINITE_REFUSALS[retryError.code] ?? retryError.message)
                  return
                }
              }
            } else if (error.status === 401 || error.status === 403) {
              fail(ballotId, error.code, error.message)
              return
            }
          }
          await sleep(Math.min(10_000, 1000 * 2 ** Math.min(attempt - 1, 4)) * (0.6 + Math.random() * 0.4))
        }
      } finally {
        running.current.delete(ballotId)
      }
    },
    [record, fail],
  )

  // Keep local state aligned with the server's view; resume any interrupted attempt.
  useEffect(() => {
    if (!detail) return
    const drafts: Record<string, string | null> = {}
    for (const ballot of detail.ballots) {
      drafts[ballot.id] = storage(participantId)?.getItem(draftKey(participantId ?? '', ballot.id)) ?? null
      if (ballot.receipt) {
        storage(participantId)?.removeItem(pendingKey(participantId ?? '', ballot.id))
        storage(participantId)?.removeItem(draftKey(participantId ?? '', ballot.id))
      }
    }
    dispatch({ type: 'sync', detail, drafts })
    for (const ballot of detail.ballots) {
      if (ballot.receipt) continue
      const pending = readPending(participantId, ballot.id)
      if (pending) void reconcile(ballot.id, pending.nomineeId, pending.key)
    }
  }, [detail, reconcile, participantId])

  const select = useCallback((ballotId: string, nomineeId: string) => {
    storage(participantId)?.setItem(draftKey(participantId ?? '', ballotId), nomineeId)
    dispatch({ type: 'select', ballotId, nomineeId })
  }, [participantId])

  const submit = useCallback(
    async (ballotId: string) => {
      const current = state[ballotId]
      const nomineeId = current?.draft
      if (!nomineeId || current?.receipt) return
      if (current.submit.phase === 'submitting' || current.submit.phase === 'checking') return
      const key = newIdempotencyKey()
      storage(participantId)?.setItem(pendingKey(participantId ?? '', ballotId), JSON.stringify({ key, nomineeId }))
      dispatch({ type: 'submit', ballotId, phase: { phase: 'submitting', nomineeId, key } })
      try {
        const result = await participantApi<{ receipt: Receipt }>(`/me/ballots/${ballotId}/vote`, {
          method: 'POST',
          body: { nominee_id: nomineeId },
          idempotencyKey: key,
          timeoutMs: 12_000,
        })
        record(ballotId, result.receipt, true)
      } catch (error) {
        if (!(error instanceof ApiError)) {
          void reconcile(ballotId, nomineeId, key)
          return
        }
        if (error.isNetwork || error.status >= 500) {
          void reconcile(ballotId, nomineeId, key)
          return
        }
        const receipt = error.details.receipt as Receipt | undefined
        if (receipt && (error.code === 'ALREADY_VOTED' || error.code === 'IDEMPOTENCY_KEY_REUSED')) {
          record(ballotId, receipt, false, 'You had already voted on this ballot. Votes are final.')
          return
        }
        fail(ballotId, error.code, DEFINITE_REFUSALS[error.code] ?? error.message)
      }
    },
    [state, record, reconcile, fail, participantId],
  )

  return { state, select, submit }
}
