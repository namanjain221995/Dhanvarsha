'use client'

import { useEffect, useState } from 'react'

/**
 * Server-time clock. Countdowns and "is it open yet" displays use the server's clock,
 * never the device clock; the server enforces every rule regardless.
 */
class ServerClock {
  private offsetMs = 0
  private bestRtt = Number.POSITIVE_INFINITY
  private syncedAt = 0

  /** Record a server timestamp observed during a request that started and ended locally. */
  observe(serverIso: string | null | undefined, startedAt: number, endedAt: number) {
    if (!serverIso) return
    const server = Date.parse(serverIso)
    if (Number.isNaN(server)) return
    const rtt = Math.max(endedAt - startedAt, 0)
    const stale = Date.now() - this.syncedAt > 60_000
    // Prefer low-latency samples; refresh at least once a minute.
    if (rtt <= this.bestRtt * 1.5 || stale) {
      this.offsetMs = server + rtt / 2 - endedAt
      this.bestRtt = stale ? rtt : Math.min(this.bestRtt, rtt)
      this.syncedAt = Date.now()
    }
  }

  now(): number {
    return Date.now() + this.offsetMs
  }
}

export const serverClock = new ServerClock()

/** Re-render every `intervalMs`, returning server time in ms. */
export function useServerNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => serverClock.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(serverClock.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

export function formatInZone(
  iso: string | null | undefined,
  timeZone: string,
  options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' },
): string {
  if (!iso) return ''
  return new Intl.DateTimeFormat('en-IN', { timeZone, ...options }).format(new Date(iso))
}

export function zoneAbbreviation(timeZone: string, iso?: string | null): string {
  const parts = new Intl.DateTimeFormat('en-IN', { timeZone, timeZoneName: 'short' }).formatToParts(
    iso ? new Date(iso) : new Date(),
  )
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? timeZone
}

export function formatTime(iso: string | null | undefined, timeZone: string): string {
  return formatInZone(iso, timeZone, { hour: 'numeric', minute: '2-digit' })
}

/** The viewer's own zone, when it differs from the programme zone. */
export function localEquivalent(iso: string | null | undefined, programmeZone: string): string | null {
  if (!iso) return null
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (!local || local === programmeZone) return null
  const a = formatInZone(iso, local, { hour: 'numeric', minute: '2-digit', weekday: 'short' })
  return `${a} your time`
}

export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export function describeCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const parts = []
  if (h) parts.push(`${h} hour${h === 1 ? '' : 's'}`)
  if (m) parts.push(`${m} minute${m === 1 ? '' : 's'}`)
  if (!h && s) parts.push(`${s} second${s === 1 ? '' : 's'}`)
  return parts.join(' ') || 'less than a second'
}
