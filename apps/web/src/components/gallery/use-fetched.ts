'use client'

import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '@/lib/api'
import { galleryApi, isEnrolRequired } from '@/lib/gallery'

type Query = Record<string, string | number>
type Result<T> = { key: string | null; path: string | null; data: T | null; error: string | null }

/**
 * One gallery request. While a new query loads (a longer list, another search) the last answer for
 * the same path stays on screen. `pollMs` asks again quietly while the tab is visible. A browser
 * whose cookie is gone goes back to the form.
 */
export function useFetched<T>(path: string | null, query: Query = {}, pollMs = 0) {
  const key = path ? JSON.stringify([path, query]) : null
  const [result, setResult] = useState<Result<T>>({ key: null, path: null, data: null, error: null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!key) return
    const [target, parameters] = JSON.parse(key) as [string, Query]
    const controller = new AbortController()
    galleryApi<T>(target, { query: parameters, signal: controller.signal, timeoutMs: 30_000 })
      .then((data) => setResult({ key, path: target, data, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        if (isEnrolRequired(error)) {
          window.location.reload()
          return
        }
        const message = error instanceof ApiError ? error.message : 'Could not load this. Try again.'
        setResult((previous) => ({ key, path: target, data: previous.path === target ? previous.data : null, error: message }))
      })
    return () => controller.abort()
  }, [key, attempt])

  useEffect(() => {
    if (!pollMs) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setAttempt((n) => n + 1)
    }, pollMs)
    return () => window.clearInterval(timer)
  }, [pollMs])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  const samePath = result.path === path
  return { data: samePath ? result.data : null, error: samePath ? result.error : null, loading: result.key !== key, reload }
}
