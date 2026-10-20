import { formatInZone, zoneAbbreviation } from '@/lib/time'

/**
 * Programme-timezone helpers. `<input type="datetime-local">` holds a wall-clock value
 * with no zone; the console always reads and writes it in the programme timezone (never
 * the device zone) and converts to UTC ISO strings for the API.
 */

const formatters = new Map<string, Intl.DateTimeFormat>()

function partsFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = formatters.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    formatters.set(timeZone, formatter)
  }
  return formatter
}

function wallClock(ms: number, timeZone: string) {
  const parts = partsFormatter(timeZone).formatToParts(new Date(ms))
  const get = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour') % 24,
    minute: get('minute'),
    second: get('second'),
  }
}

/** Offset of `timeZone` from UTC at instant `ms`, in milliseconds. */
function offsetAt(ms: number, timeZone: string): number {
  const w = wallClock(ms, timeZone)
  const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second)
  return asUtc - Math.floor(ms / 1000) * 1000
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "2026-10-20T19:30" read as wall-clock time in `timeZone` → UTC ISO string. */
export function zonedInputToIso(value: string, timeZone: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim())
  if (!m || !isValidTimeZone(timeZone)) return null
  const guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6] ?? 0))
  if (Number.isNaN(guess)) return null
  // Two passes settle daylight-saving transitions.
  let ms = guess - offsetAt(guess, timeZone)
  ms = guess - offsetAt(ms, timeZone)
  return new Date(ms).toISOString()
}

/** UTC ISO string → "YYYY-MM-DDTHH:mm" wall-clock value in `timeZone`. */
export function isoToZonedInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso || !isValidTimeZone(timeZone)) return ''
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return ''
  const w = wallClock(ms, timeZone)
  return `${w.year}-${pad(w.month)}-${pad(w.day)}T${pad(w.hour)}:${pad(w.minute)}`
}

export function msToZonedInput(ms: number, timeZone: string): string {
  return isoToZonedInput(new Date(ms).toISOString(), timeZone)
}

export function isValidTimeZone(timeZone: string): boolean {
  if (!timeZone || !timeZone.trim()) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone })
    return true
  } catch {
    return false
  }
}

let zoneList: string[] | null = null

export function timeZoneNames(): string[] {
  if (!zoneList) {
    try {
      zoneList = Intl.supportedValuesOf('timeZone')
    } catch {
      zoneList = ['Asia/Kolkata', 'UTC', 'Europe/London', 'America/New_York']
    }
  }
  return zoneList
}

/**
 * The spelling to send to the server, whose IANA lookup is case-sensitive. A valid name
 * is kept as typed (browsers may report aliases such as Asia/Calcutta for Asia/Kolkata,
 * so the browser's own canonical form is never substituted); only the letter case is
 * fixed when the browser's list has the same name in another case.
 */
export function canonicalTimeZone(input: string): string | null {
  const timeZone = input.trim()
  if (!isValidTimeZone(timeZone)) return null
  const names = timeZoneNames()
  if (names.includes(timeZone)) return timeZone
  const lower = timeZone.toLowerCase()
  return names.find((name) => name.toLowerCase() === lower) ?? timeZone
}

// ---- display ------------------------------------------------------------------

/** "7:30 pm" — or "Tue 20 Oct, 7:30 pm" when not on the same programme-local day as `reference`. */
export function formatWhen(iso: string | null | undefined, timeZone: string, reference?: number): string {
  if (!iso) return ''
  const sameDay =
    reference !== undefined &&
    formatInZone(iso, timeZone, { dateStyle: 'short' }) ===
      formatInZone(new Date(reference).toISOString(), timeZone, { dateStyle: 'short' })
  if (sameDay) return formatInZone(iso, timeZone, { hour: 'numeric', minute: '2-digit' })
  return formatInZone(iso, timeZone, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

/** "20 Oct 2026, 7:30:05 pm" — for logs, where seconds matter. */
export function formatStamp(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return ''
  return formatInZone(iso, timeZone, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatClock(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return ''
  return formatInZone(iso, timeZone, { hour: 'numeric', minute: '2-digit', second: '2-digit' })
}

export function zoneLabel(timeZone: string, iso?: string | null): string {
  if (!isValidTimeZone(timeZone)) return timeZone
  const abbr = zoneAbbreviation(timeZone, iso)
  return abbr && abbr !== timeZone ? `${timeZone} (${abbr})` : timeZone
}

const relative = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** "in 5 minutes", "2 hours ago". */
export function formatRelative(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return ''
  const diff = Date.parse(iso) - nowMs
  const abs = Math.abs(diff)
  if (abs < 45_000) return diff >= 0 ? 'in a few seconds' : 'just now'
  if (abs < 3_600_000) return relative.format(Math.round(diff / 60_000), 'minute')
  if (abs < 86_400_000) return relative.format(Math.round(diff / 3_600_000), 'hour')
  return relative.format(Math.round(diff / 86_400_000), 'day')
}
