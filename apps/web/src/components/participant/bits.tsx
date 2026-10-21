'use client'

import { Check, CircleSlash, Clock3, Lock, LogOut, RefreshCw, WifiOff } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { Avatar } from '@/components/ui/misc'
import type { LiveStatus } from '@/lib/live'
import { describeCountdown, formatCountdown, formatTime, useServerNow, zoneAbbreviation } from '@/lib/time'
import type { PublicProgramme } from '@/lib/types'
import { ProgrammeLogo } from './programme-logo'

export type ChipKind = 'live' | 'upcoming' | 'recorded' | 'closed' | 'partial'

/** Status uses an icon and words, never colour alone. */
export function StatusChip({ kind, label }: { kind: ChipKind; label?: string }) {
  const styles: Record<ChipKind, string> = {
    live: 'bg-primary-wash text-primary',
    upcoming: 'bg-plum/[0.06] text-plum-soft',
    recorded: 'bg-success-wash text-success',
    closed: 'bg-plum/[0.06] text-plum-soft',
    partial: 'bg-gold-wash text-gold-deep',
  }
  const text: Record<ChipKind, string> = {
    live: 'Live now',
    upcoming: 'Upcoming',
    recorded: 'Vote recorded',
    closed: 'Voting closed',
    partial: 'Vote pending',
  }
  const icon: Record<ChipKind, React.ReactNode> = {
    live: <span aria-hidden className="live-lamp" />,
    upcoming: <Lock aria-hidden className="size-3.5" />,
    recorded: <Check aria-hidden className="size-3.5" strokeWidth={3} />,
    closed: <CircleSlash aria-hidden className="size-3.5" />,
    partial: <Clock3 aria-hidden className="size-3.5" />,
  }
  return (
    <span
      data-chip={kind}
      className={`inline-flex min-h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-[0.8rem] font-semibold ${styles[kind]}`}
    >
      {icon[kind]}
      {label ?? text[kind]}
    </span>
  )
}

export function EventLogo({
  src,
  name,
  size,
  transitionId,
  locked = false,
}: {
  src: string | null
  name: string | null
  size: number
  transitionId?: string
  locked?: boolean
}) {
  if (locked) {
    return (
      <span
        aria-hidden
        className="inline-flex shrink-0 items-center justify-center rounded-[16px] border border-dashed border-sand-strong bg-ivory text-plum-faint"
        style={{ width: size, height: size }}
      >
        <Lock className="size-5" />
      </span>
    )
  }
  return (
    <span style={transitionId ? { viewTransitionName: transitionId } : undefined} className="gold-frame gold-frame-tile">
      <Avatar src={src} name={name} size={size} rounded="xl" />
    </span>
  )
}

/** Countdown to a server-side deadline. Announces nothing on every tick. */
export function Countdown({ deadline, className = '' }: { deadline: string; className?: string }) {
  const now = useServerNow(1000)
  const remaining = Date.parse(deadline) - now
  if (remaining <= 0) return <span className={className}>Closed</span>
  return (
    <span className={`tabular ${className}`}>
      <span aria-hidden>{formatCountdown(remaining)}</span>
      <span className="sr-only">{describeCountdown(remaining)} left</span>
    </span>
  )
}

export function ConnectionBanner({
  connection,
  authoritative,
  stale,
  onRetry,
}: {
  connection: LiveStatus
  authoritative: boolean
  stale: boolean
  onRetry: () => void
}) {
  // Short blips stay quiet; show the banner only after ~2.5s without confirmed state.
  const [visible, setVisible] = useState(false)
  const show = !authoritative || (connection !== 'live' && connection !== 'connecting')
  useEffect(() => {
    if (!show) {
      const id = window.setTimeout(() => setVisible(false), 0)
      return () => window.clearTimeout(id)
    }
    const id = window.setTimeout(() => setVisible(true), 2500)
    return () => window.clearTimeout(id)
  }, [show])
  if (!visible || !show) return null
  const offline = connection === 'offline'
  return (
    <div
      role="status"
      className="enter-fade flex items-center gap-3 rounded-2xl border border-gold/40 bg-gold-wash px-4 py-3 text-[0.92rem] text-plum"
    >
      {offline ? (
        <WifiOff aria-hidden className="size-5 shrink-0 text-gold-deep" />
      ) : (
        <RefreshCw aria-hidden className="size-5 shrink-0 animate-spin text-gold-deep [animation-duration:2.4s]" />
      )}
      <p className="min-w-0 flex-1">
        {offline ? 'You are offline. ' : 'Reconnecting… '}
        {authoritative ? 'Updates may be a little late.' : 'Voting pauses until we can confirm the latest state.'}
      </p>
      {stale || offline ? (
        <button
          type="button"
          onClick={onRetry}
          className="min-h-11 shrink-0 rounded-full px-3 text-sm font-semibold text-primary hover:bg-primary-wash"
        >
          Retry
        </button>
      ) : null}
    </div>
  )
}

export function ConnectionDot({ connection }: { connection: LiveStatus }) {
  const label =
    connection === 'live'
      ? 'Connected'
      : connection === 'offline'
        ? 'Offline'
        : connection === 'connecting'
          ? 'Connecting'
          : 'Reconnecting'
  const color = connection === 'live' ? 'bg-success' : connection === 'offline' ? 'bg-danger' : 'bg-gold'
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-plum-soft">
      <span aria-hidden className={`size-2 rounded-full ${color}`} />
      {label}
    </span>
  )
}

export function ParticipantHeader({
  programme,
  displayName,
  userCode,
  connection,
  onSignOut,
}: {
  programme: PublicProgramme
  displayName: string
  userCode: string
  connection: LiveStatus
  onSignOut: () => void
}) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return
      if (event instanceof MouseEvent && menuRef.current?.contains(event.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])
  return (
    // A bar attached to the top of the screen with rounded lower corners. On a phone it runs
    // edge to edge (the negative margins undo the page padding); it also covers the notch area.
    <header className="-mx-4 flex items-center gap-3 rounded-b-[1.5rem] border-b border-sand bg-paper px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))] shadow-[var(--shadow-card)] sm:-mx-5 sm:border-x sm:px-5">
      <Link href="/lobby" className="flex min-w-0 flex-1 items-center gap-3 rounded-xl">
        <ProgrammeLogo src={programme.logo_url} name={programme.name} height={44} />
        <span className="min-w-0">
          <span className="type-condensed block truncate text-[1.02rem] font-bold leading-tight">{programme.name}</span>
          <ConnectionDot connection={connection} />
        </span>
      </Link>
      <div className="relative" ref={menuRef}>
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`Account menu for ${displayName || userCode}`}
          onClick={() => setOpen((v) => !v)}
          className="flex size-11 items-center justify-center rounded-full"
        >
          <span className="gold-frame">
            <Avatar name={displayName || userCode} size={36} />
          </span>
        </button>
        {open ? (
          <div
            role="menu"
            className="enter-fade absolute -right-2 top-[3.6rem] z-30 w-64 rounded-2xl border border-sand bg-paper p-2 shadow-[var(--shadow-lift)]"
          >
            <div className="px-3 py-2">
              <p className="truncate font-semibold">{displayName || 'Participant'}</p>
              <p className="text-sm text-plum-soft">
                User ID <span className="tabular font-semibold text-plum">{userCode}</span>
              </p>
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={onSignOut}
              className="flex min-h-11 w-full items-center gap-2 rounded-xl px-3 text-left font-semibold text-plum hover:bg-plum/5"
            >
              <LogOut aria-hidden className="size-4" />
              Sign out
            </button>
          </div>
        ) : null}
      </div>
    </header>
  )
}

export function timeLabel(iso: string | null | undefined, tz: string): string {
  if (!iso) return ''
  return `${formatTime(iso, tz)} ${zoneAbbreviation(tz, iso)}`
}

/** Display size that keeps long round names to about three lines on a 320px phone. */
export function titleSize(name: string | null | undefined, size: 'feature' | 'detail'): string {
  const length = name?.length ?? 0
  if (size === 'feature') {
    if (length > 48) return 'text-[1.55rem] sm:text-[1.9rem]'
    if (length > 28) return 'text-[1.75rem] sm:text-[2.1rem]'
    return 'text-[1.95rem] sm:text-[2.25rem]'
  }
  if (length > 48) return 'text-[1.6rem] sm:text-[2rem]'
  if (length > 28) return 'text-[1.8rem] sm:text-[2.2rem]'
  return 'text-[2rem] sm:text-[2.35rem]'
}

export function PageColumn({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-[560px] px-4 sm:px-5 ${className}`}>{children}</div>
}
