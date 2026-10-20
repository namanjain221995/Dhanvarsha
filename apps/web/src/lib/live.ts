'use client'

import { useEffect, useRef, useState } from 'react'

/**
 * Live channel: a WebSocket that only carries "something changed" signals. Every
 * signal makes the page refetch an authoritative snapshot over HTTP.
 *
 * - Reconnects with exponential backoff plus jitter (1s .. 30s).
 * - Treats 45s of silence as a dead connection (the server pings every 20s).
 * - Falls back to polling every 15s while the socket is down.
 * - Refetches on reconnect, when the tab becomes visible and when the browser goes online.
 * - A 4401 close means the session ended: no reconnect.
 */

export type LiveStatus = 'connecting' | 'live' | 'reconnecting' | 'offline'

export type LiveSignal =
  | { type: 'hello'; v?: number; lv?: number }
  | { type: 'state'; v?: number; lv?: number; ts?: number }
  | { type: 'tally'; events?: string[] }
  | { type: 'email' }
  | { type: 'resync'; reason?: string }
  | { type: 'revoked'; reason?: string }
  | { type: 'poll' }
  | { type: 'visible' }

const SILENCE_MS = 45_000
const POLL_MS = 15_000
const CLIENT_PING_MS = 25_000

export function useLiveChannel(path: '/ws' | '/ws/admin', onSignal: (signal: LiveSignal) => void, enabled = true) {
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const handler = useRef(onSignal)
  useEffect(() => {
    handler.current = onSignal
  }, [onSignal])

  useEffect(() => {
    if (!enabled) return
    let socket: WebSocket | null = null
    let attempt = 0
    let reconnectTimer: number | undefined
    let pollTimer: number | undefined
    let stopped = false
    let lastMessage = Date.now()

    const emit = (signal: LiveSignal) => handler.current(signal)

    const startPolling = () => {
      if (pollTimer !== undefined) return
      pollTimer = window.setInterval(() => emit({ type: 'poll' }), POLL_MS)
    }
    const stopPolling = () => {
      if (pollTimer !== undefined) window.clearInterval(pollTimer)
      pollTimer = undefined
    }

    const scheduleReconnect = () => {
      if (stopped) return
      window.clearTimeout(reconnectTimer)
      const base = Math.min(30_000, 1000 * 2 ** attempt)
      const delay = base / 2 + Math.random() * (base / 2)
      attempt += 1
      reconnectTimer = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (stopped) return
      window.clearTimeout(reconnectTimer)
      if (!navigator.onLine) {
        setStatus('offline')
        startPolling()
        return
      }
      setStatus(attempt === 0 ? 'connecting' : 'reconnecting')
      if (socket) {
        // Never keep two sockets: drop the previous one before reconnecting.
        const previous = socket
        socket = null
        previous.close(1000)
      }
      const url = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${path}`
      let ws: WebSocket
      try {
        ws = new WebSocket(url)
      } catch {
        scheduleReconnect()
        return
      }
      socket = ws
      ws.onopen = () => {
        attempt = 0
        lastMessage = Date.now()
        setStatus('live')
        stopPolling()
      }
      ws.onmessage = (event) => {
        lastMessage = Date.now()
        try {
          const message = JSON.parse(String(event.data)) as LiveSignal | { type: 'ping' | 'pong' }
          if (message.type === 'ping' || message.type === 'pong') return
          emit(message as LiveSignal)
        } catch {
          /* ignore malformed frames */
        }
      }
      ws.onclose = (event) => {
        if (socket !== ws) return
        socket = null
        if (event.code === 4401) {
          stopped = true
          setStatus('offline')
          emit({ type: 'revoked', reason: event.reason || 'session' })
          return
        }
        setStatus(navigator.onLine ? 'reconnecting' : 'offline')
        startPolling()
        scheduleReconnect()
      }
      ws.onerror = () => {
        /* onclose follows */
      }
    }

    const pingTimer = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping', t: Date.now() }))
    }, CLIENT_PING_MS)
    const watchdog = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN && Date.now() - lastMessage > SILENCE_MS) socket.close()
    }, 5000)

    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      emit({ type: 'visible' })
      if (!socket || socket.readyState === WebSocket.CLOSED) {
        attempt = 0
        connect()
      }
    }
    const onOnline = () => {
      attempt = 0
      emit({ type: 'visible' })
      connect()
    }
    const onOffline = () => {
      setStatus('offline')
      startPolling()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    connect()

    return () => {
      stopped = true
      window.clearTimeout(reconnectTimer)
      window.clearInterval(pingTimer)
      window.clearInterval(watchdog)
      stopPolling()
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
      if (socket) {
        const s = socket
        socket = null
        s.close(1000)
      }
    }
  }, [path, enabled])

  return status
}
