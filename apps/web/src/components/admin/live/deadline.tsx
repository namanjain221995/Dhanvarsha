'use client'

import { useEffect, useId, useRef } from 'react'
import { formatWhen, isoToZonedInput, msToZonedInput, zonedInputToIso, zoneLabel } from '@/lib/admin/zoned'
import { describeCountdown, formatCountdown, useServerNow } from '@/lib/time'
import { controlBase } from '../form'

export type DeadlineMode = 'access' | 'in' | 'at' | 'extend'
export type DeadlineChoice = { mode: DeadlineMode; extendBy: string; inMinutes: string; at: string }

type Context = {
  nowMs: number
  timeZone: string
  accessCloseIso: string | null
  currentIso?: string | null
  /** Send programme access close as an explicit time (deadline changes need one). */
  explicitAccess?: boolean
}

const MIN_LEAD_MS = 15_000

/** A starting choice that is valid right now, preferring the first of `modes`. */
export function initialDeadline(options: {
  modes: DeadlineMode[]
  accessCloseIso: string | null
  timeZone: string
  nowMs: number
  currentIso?: string | null
}): DeadlineChoice {
  const { modes, accessCloseIso, timeZone, nowMs, currentIso } = options
  const closeMs = accessCloseIso ? Date.parse(accessCloseIso) : Number.POSITIVE_INFINITY
  const atMs = Math.min(Math.max(currentIso ? Date.parse(currentIso) : 0, nowMs + 30 * 60_000), closeMs - 60_000)
  const base: DeadlineChoice = {
    mode: modes[0] ?? 'in',
    extendBy: '5',
    inMinutes: '15',
    at: msToZonedInput(Math.max(atMs, nowMs + 2 * 60_000), timeZone),
  }
  const context: Context = { nowMs, timeZone, accessCloseIso, currentIso }
  for (const mode of modes) {
    const candidate = { ...base, mode }
    if (!resolveDeadline(candidate, context).error) return candidate
  }
  return base
}

/**
 * Turn the organiser's choice into an absolute deadline.
 * `iso: null` with no error means "let the server use programme access close".
 */
export function resolveDeadline(choice: DeadlineChoice, context: Context): { iso: string | null; error: string | null } {
  const { nowMs, timeZone, accessCloseIso, currentIso, explicitAccess } = context
  let iso: string | null
  if (choice.mode === 'access') {
    if (!accessCloseIso) return { iso: null, error: 'Programme access has no closing time. Choose another deadline.' }
    if (Date.parse(accessCloseIso) <= nowMs + MIN_LEAD_MS) {
      return { iso: null, error: 'Programme access closes too soon. Extend access in Programme settings first.' }
    }
    return { iso: explicitAccess ? accessCloseIso : null, error: null }
  }
  if (choice.mode === 'in' || choice.mode === 'extend') {
    const minutes = Number(choice.mode === 'in' ? choice.inMinutes : choice.extendBy)
    if (!Number.isFinite(minutes) || minutes <= 0) return { iso: null, error: 'Enter a number of minutes greater than 0.' }
    if (minutes > 24 * 60) return { iso: null, error: 'Use 1,440 minutes (24 hours) or fewer.' }
    const from = choice.mode === 'extend' && currentIso ? Math.max(Date.parse(currentIso), nowMs) : nowMs
    iso = new Date(from + minutes * 60_000).toISOString()
  } else {
    iso = choice.at ? zonedInputToIso(choice.at, timeZone) : null
    if (!iso) return { iso: null, error: 'Enter a complete date and time.' }
  }
  const ms = Date.parse(iso)
  if (ms <= nowMs + MIN_LEAD_MS) return { iso: null, error: 'The deadline must be at least 15 seconds from now.' }
  if (accessCloseIso && ms > Date.parse(accessCloseIso)) {
    return {
      iso: null,
      error: `That is after programme access closes (${formatWhen(accessCloseIso, timeZone, nowMs)}). Choose an earlier time, or extend access in Programme settings.`,
    }
  }
  return { iso, error: null }
}

export function DeadlinePicker({
  value,
  onChange,
  modes,
  timeZone,
  accessCloseIso,
  currentIso,
  legend = 'Voting deadline',
}: {
  value: DeadlineChoice
  onChange: (value: DeadlineChoice) => void
  modes: DeadlineMode[]
  timeZone: string
  accessCloseIso: string | null
  currentIso?: string | null
  legend?: string
}) {
  const id = useId()
  const now = useServerNow(1000)
  const result = resolveDeadline(value, { nowMs: now, timeZone, accessCloseIso, currentIso })
  const effective = value.mode === 'access' && !result.error ? accessCloseIso : result.iso
  const set = (patch: Partial<DeadlineChoice>) => onChange({ ...value, ...patch })

  const option = (mode: DeadlineMode, label: React.ReactNode, extra?: React.ReactNode, disabled = false) =>
    modes.includes(mode) ? (
      <div
        className={`rounded-2xl border px-3 py-1.5 transition-colors ${
          value.mode === mode ? 'border-plum bg-paper ring-1 ring-plum' : 'border-sand-strong bg-paper'
        } ${disabled ? 'opacity-60' : ''}`}
      >
        <label className="flex min-h-11 cursor-pointer items-center gap-3">
          <input
            type="radio"
            name={`${id}-mode`}
            checked={value.mode === mode}
            disabled={disabled}
            onChange={() => set({ mode })}
            className="size-4 shrink-0 accent-plum"
          />
          <span className="min-w-0 text-[0.95rem] font-semibold text-plum">{label}</span>
        </label>
        {extra ? <div className="pb-2 pl-7">{extra}</div> : null}
      </div>
    ) : null

  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-sm font-semibold text-plum">{legend}</legend>
      <div className="space-y-2">
        {option(
          'extend',
          <>Extend the current deadline</>,
          <MinutesInput
            value={value.extendBy}
            onChange={(extendBy) => set({ mode: 'extend', extendBy })}
            onFocus={() => value.mode !== 'extend' && set({ mode: 'extend' })}
            suffix="minutes later"
          />,
        )}
        {option(
          'access',
          accessCloseIso ? (
            <>When programme access closes, {formatWhen(accessCloseIso, timeZone, now)}</>
          ) : (
            <>When programme access closes (not set)</>
          ),
          undefined,
          !accessCloseIso,
        )}
        {option(
          'in',
          <>A number of minutes from now</>,
          <MinutesInput
            value={value.inMinutes}
            onChange={(inMinutes) => set({ mode: 'in', inMinutes })}
            onFocus={() => value.mode !== 'in' && set({ mode: 'in' })}
            suffix="minutes from now"
          />,
        )}
        {option(
          'at',
          <>At a set time</>,
          <div>
            <input
              type="datetime-local"
              step={60}
              aria-label={`Deadline, ${zoneLabel(timeZone)} time`}
              value={value.at}
              min={isoToZonedInput(new Date(now).toISOString(), timeZone)}
              onFocus={() => value.mode !== 'at' && set({ mode: 'at' })}
              onChange={(event) => set({ mode: 'at', at: event.target.value })}
              className={`${controlBase} tabular min-h-11 w-full max-w-xs border-plum-faint/70`}
            />
            <p className="mt-1 text-sm text-plum-soft">Time in {zoneLabel(timeZone)}.</p>
          </div>,
        )}
      </div>
      <p aria-live="polite" className={`mt-3 text-sm ${result.error ? 'font-medium text-danger' : 'text-plum-soft'}`}>
        {result.error
          ? result.error
          : effective
            ? `Voting will close at ${formatWhen(effective, timeZone, now)}, ${describeCountdown(Date.parse(effective) - now)} from now.`
            : null}
      </p>
    </fieldset>
  )
}

function MinutesInput({
  value,
  onChange,
  onFocus,
  suffix,
}: {
  value: string
  onChange: (value: string) => void
  onFocus: () => void
  suffix: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <label className="flex items-center gap-2">
        <input
          type="number"
          inputMode="numeric"
          min={1}
          max={1440}
          step={1}
          value={value}
          onFocus={onFocus}
          onChange={(event) => onChange(event.target.value)}
          className={`${controlBase} tabular min-h-11 w-20 border-plum-faint/70 px-3`}
        />
        <span className="text-sm text-plum-soft">{suffix}</span>
      </label>
      <span className="flex gap-1.5">
        {[2, 5, 10, 15].map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => onChange(String(n))}
            aria-label={`${n} ${suffix}`}
            aria-pressed={value === String(n)}
            className={`tabular min-h-11 min-w-11 rounded-full border px-2 text-sm font-semibold ${
              value === String(n) ? 'border-plum bg-plum text-paper' : 'border-sand-strong text-plum hover:border-plum-soft'
            }`}
          >
            {n}
          </button>
        ))}
      </span>
    </div>
  )
}

/** Countdown on the server clock. Calls `onExpire` once when it reaches zero. */
export function TimeLeft({ deadline, onExpire, className = '' }: { deadline: string; onExpire?: () => void; className?: string }) {
  const now = useServerNow(1000)
  const ms = Date.parse(deadline) - now
  const armed = useRef(false)
  useEffect(() => {
    if (ms > 0) {
      armed.current = true
      return
    }
    if (armed.current) {
      armed.current = false
      onExpire?.()
    }
  }, [ms, onExpire])
  if (ms <= 0) return <span className={className}>Deadline passed</span>
  return (
    <span className={`tabular ${className}`}>
      <span aria-hidden>{formatCountdown(ms)}</span>
      <span className="sr-only">{describeCountdown(ms)} left</span>
    </span>
  )
}
