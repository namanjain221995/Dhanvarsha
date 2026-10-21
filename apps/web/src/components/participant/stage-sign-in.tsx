'use client'

import { Eye, EyeOff, LoaderCircle, LockKeyhole, UserRound } from 'lucide-react'
import { useEffect, useId, useState, useSyncExternalStore } from 'react'
import { formatInZone, zoneAbbreviation } from '@/lib/time'
import type { Accent, PublicProgramme } from '@/lib/types'
import { OpeningCountdown } from './opening-countdown'
import type { SignInForm } from './sign-in'

/*
 * The gold look's sign-in: a night-blue arena with gold light, a ticket emblem, and a lit
 * stage at the foot of the page. The scene is decoration only (hidden from assistive
 * technology); the form keeps exactly the classic page's behaviour.
 */

const ACCENT_KEY = 'sv:accent'

/** The look this browser last showed, so the loading state does not flash the wrong colours. */
export function useRememberedAccent(current: Accent | undefined): string | null {
  useEffect(() => {
    if (!current) return
    try {
      window.localStorage.setItem(ACCENT_KEY, current)
    } catch {
      /* private mode: the classic loading state is fine */
    }
  }, [current])
  return useSyncExternalStore(
    () => () => {},
    () => {
      try {
        return window.localStorage.getItem(ACCENT_KEY)
      } catch {
        return null
      }
    },
    () => null,
  )
}

function TicketEmblem() {
  return (
    <svg className="stage-emblem" viewBox="0 0 120 120" aria-hidden focusable="false">
      <defs>
        <linearGradient id="stage-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fff1b5" />
          <stop offset="0.35" stopColor="#f2c14e" />
          <stop offset="0.7" stopColor="#b8801a" />
          <stop offset="1" stopColor="#f7d36a" />
        </linearGradient>
        <radialGradient id="stage-emblem-fill" cx="50%" cy="40%" r="60%">
          <stop offset="0" stopColor="#16307f" />
          <stop offset="1" stopColor="#060f36" />
        </radialGradient>
      </defs>
      <circle cx="60" cy="60" r="55" fill="url(#stage-emblem-fill)" />
      <circle className="stage-emblem-ring" cx="60" cy="60" r="55" fill="none" stroke="url(#stage-gold)" strokeWidth="2.2" pathLength="100" />
      <circle cx="60" cy="60" r="50" fill="none" stroke="#f2c14e" strokeOpacity="0.18" strokeWidth="1" />
      <g transform="rotate(-35 60 60)">
        <g className="stage-ticket">
          <path d="M27 42 H93 V53 A7 7 0 0 0 93 67 V78 H27 V67 A7 7 0 0 0 27 53 Z" fill="url(#stage-gold)" />
          <rect x="38" y="49" width="38" height="22" rx="2" fill="none" stroke="#8a5d0c" strokeWidth="1.6" opacity="0.75" />
          <line x1="83" y1="45" x2="83" y2="75" stroke="#8a5d0c" strokeWidth="1.4" strokeDasharray="2.4 2.4" opacity="0.7" />
          <path d="M30 44 H90" stroke="#fff6cf" strokeWidth="1.2" opacity="0.7" />
        </g>
      </g>
    </svg>
  )
}

/** Gold light sweeping down from the top corners, like stage curtains drawn in light. */
function StageArcs() {
  return (
    <svg className="stage-arcs" viewBox="0 0 400 320" preserveAspectRatio="xMidYMin slice" aria-hidden focusable="false">
      <defs>
        <linearGradient id="stage-arc" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe6a0" stopOpacity="0.95" />
          <stop offset="0.6" stopColor="#d9a43a" stopOpacity="0.5" />
          <stop offset="1" stopColor="#d9a43a" stopOpacity="0" />
        </linearGradient>
        <filter id="stage-arc-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="2.4" />
        </filter>
      </defs>
      {[0, 1].map((side) => (
        <g key={side} transform={side ? 'translate(400 0) scale(-1 1)' : undefined}>
          <g filter="url(#stage-arc-glow)" opacity="0.7">
            <path d="M-10 250 C 20 150, 60 70, 150 -10" stroke="url(#stage-arc)" strokeWidth="5" fill="none" />
          </g>
          <path d="M-10 250 C 20 150, 60 70, 150 -10" stroke="url(#stage-arc)" strokeWidth="1.4" fill="none" />
          <path d="M-14 300 C 10 190, 40 100, 120 -10" stroke="url(#stage-arc)" strokeWidth="0.9" fill="none" opacity="0.6" />
          <path d="M-6 200 C 30 120, 80 50, 185 -10" stroke="url(#stage-arc)" strokeWidth="0.7" fill="none" opacity="0.45" />
        </g>
      ))}
    </svg>
  )
}

/** Keeps names like "Dhanvarsha Live" on one line on a phone; very long names wrap. */
function titleSize(name: string): string | undefined {
  if (name.length > 22) return 'clamp(1.45rem, 6vw, 2.1rem)'
  if (name.length > 11) return 'clamp(1.6rem, 7.2vw, 2.5rem)'
  return undefined
}

const BEAMS = [-62, -44, -27, -12, 0, 12, 27, 44, 62]
const DUST = Array.from({ length: 12 }, (_, i) => ({
  left: 12 + ((i * 37) % 76),
  bottom: 22 + ((i * 13) % 30),
  delay: -((i * 0.83) % 6),
  duration: 4.5 + ((i * 0.7) % 3),
  size: 2 + (i % 3),
}))

/** The arena: spotlight beams, rows of bulbs, and a round stage ringed with gold. */
function Stage() {
  return (
    <div className="stage-floor" aria-hidden>
      <svg viewBox="0 0 400 240" preserveAspectRatio="xMidYMax slice" focusable="false">
        <defs>
          <linearGradient id="stage-beam" x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor="#dfe6ff" stopOpacity="0.55" />
            <stop offset="0.55" stopColor="#9fb4ff" stopOpacity="0.14" />
            <stop offset="1" stopColor="#9fb4ff" stopOpacity="0" />
          </linearGradient>
          <radialGradient id="stage-floor-fill" cx="50%" cy="45%" r="60%">
            <stop offset="0" stopColor="#2a4fc4" />
            <stop offset="0.45" stopColor="#11287a" />
            <stop offset="1" stopColor="#060f36" />
          </radialGradient>
          <radialGradient id="stage-spot" cx="50%" cy="50%" r="50%">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.85" />
            <stop offset="0.4" stopColor="#bcd0ff" stopOpacity="0.4" />
            <stop offset="1" stopColor="#bcd0ff" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="stage-rim" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#9a6b12" />
            <stop offset="0.3" stopColor="#ffe08a" />
            <stop offset="0.5" stopColor="#fff6d2" />
            <stop offset="0.7" stopColor="#ffe08a" />
            <stop offset="1" stopColor="#9a6b12" />
          </linearGradient>
          <filter id="stage-glow" x="-10%" y="-50%" width="120%" height="200%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
        </defs>

        {/* Spotlights rising from the stage. */}
        <g className="stage-beams">
          {BEAMS.map((angle, i) => (
            <polygon
              key={angle}
              className="stage-beam"
              style={{ animationDelay: `${-i * 0.7}s` }}
              points="196,176 204,176 222,-30 178,-30"
              fill="url(#stage-beam)"
              transform={`rotate(${angle} 200 176)`}
            />
          ))}
        </g>

        {/* Rows of bulbs around the arena. */}
        <ellipse cx="200" cy="196" rx="196" ry="66" fill="none" stroke="#f2c14e" strokeOpacity="0.55" strokeWidth="2.2" strokeDasharray="0.1 7" strokeLinecap="round" />
        <ellipse cx="200" cy="196" rx="176" ry="56" fill="none" stroke="#f2c14e" strokeOpacity="0.28" strokeWidth="1" />

        {/* The stage. */}
        <ellipse cx="200" cy="198" rx="150" ry="36" fill="url(#stage-floor-fill)" />
        <g filter="url(#stage-glow)">
          <ellipse cx="200" cy="198" rx="150" ry="36" fill="none" stroke="#ffd56b" strokeWidth="4" opacity="0.8" />
        </g>
        <ellipse cx="200" cy="198" rx="150" ry="36" fill="none" stroke="url(#stage-rim)" strokeWidth="2.2" />
        <ellipse cx="200" cy="198" rx="112" ry="26" fill="none" stroke="#f2c14e" strokeOpacity="0.55" strokeWidth="1.2" />
        <ellipse cx="200" cy="198" rx="74" ry="17" fill="none" stroke="#f2c14e" strokeOpacity="0.4" strokeWidth="1" />
        <ellipse className="stage-spotlight" cx="200" cy="196" rx="70" ry="15" fill="url(#stage-spot)" />

        {/* Footlights along the front edge. */}
        {Array.from({ length: 15 }, (_, i) => {
          const t = Math.PI * (0.12 + (0.76 * i) / 14)
          return <circle key={i} cx={200 - 150 * Math.cos(t)} cy={198 + 36 * Math.sin(t)} r="1.6" fill="#fff3c4" opacity="0.9" />
        })}
      </svg>
      {DUST.map((d, i) => (
        <span
          key={i}
          className="stage-dust"
          style={{ left: `${d.left}%`, bottom: `${d.bottom}%`, width: d.size, height: d.size, animationDelay: `${d.delay}s`, animationDuration: `${d.duration}s` }}
        />
      ))}
    </div>
  )
}

function StageInput({
  label,
  icon,
  password = false,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; icon: React.ReactNode; password?: boolean }) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[0.92rem] font-semibold text-[#eef0ff]">
        {label}
      </label>
      {/* The input is the whole field (icon and eye button sit on top of it), so browser
          autofill colours one rounded box instead of a square inside the field. */}
      <div className={`stage-input${password ? ' stage-input-password' : ''}`}>
        <span aria-hidden className="stage-input-icon">
          {icon}
        </span>
        <input
          id={id}
          type={password && !visible ? 'password' : password ? 'text' : rest.type}
          {...(password ? { autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false } : {})}
          {...rest}
        />
        {password ? (
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? 'Hide password' : 'Show password'}
            aria-pressed={visible}
            className="stage-input-toggle"
          >
            {visible ? <EyeOff aria-hidden className="size-5" /> : <Eye aria-hidden className="size-5" />}
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function StageLoading({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <div data-scene="stage" className="stage-page relative isolate flex min-h-dvh flex-col">
      <StageArcs />
      <div className="mx-auto w-full max-w-[420px] px-6 pt-16 text-center" aria-busy={!error}>
        {error ? (
          <div className="stage-note stage-note-danger" role="alert">
            <p className="font-semibold">We could not load the programme</p>
            <p className="mt-1">{error}</p>
            <button type="button" onClick={onRetry} className="stage-link mt-3">
              Try again
            </button>
          </div>
        ) : (
          <div className="mx-auto size-28 rounded-full border border-[#f2c14e]/30" />
        )}
      </div>
      <Stage />
    </div>
  )
}

export function StageSignIn({ programme, form }: { programme: PublicProgramme; form: SignInForm }) {
  const { userId, setUserId, password, setPassword, busy, formError, checkingSession, notice, submit, reload } = form
  const tz = programme.timezone
  const phase = programme.login_phase
  const open = phase === 'open'
  const disabled = !open || checkingSession
  const opensAt = programme.login_opens_at
    ? `${formatInZone(programme.login_opens_at, tz, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ${zoneAbbreviation(tz, programme.login_opens_at)}`
    : null

  return (
    <div data-accent="gold" data-scene="stage" className="stage-page relative isolate flex min-h-dvh flex-col">
      <StageArcs />
      <main className="relative mx-auto w-full max-w-[420px] px-6 pt-[max(2.75rem,env(safe-area-inset-top))]">
        <header className="text-center">
          <div className="stage-emblem-wrap">
            <TicketEmblem />
          </div>
          <h1 className="stage-title stage-rise mt-5" style={{ animationDelay: '0.25s', fontSize: titleSize(programme.name) }}>
            {programme.name}
          </h1>
          {programme.description ? (
            <p className="stage-tagline stage-rise mt-2" style={{ animationDelay: '0.35s' }}>
              {programme.description}
            </p>
          ) : null}
        </header>

        {phase === 'before_open' && programme.countdown_enabled && programme.login_opens_at ? (
          <OpeningCountdown opensAt={programme.login_opens_at} onReached={() => void reload()} tone="stage" />
        ) : null}

        <section aria-labelledby="signin-title" className="stage-rise mt-8" style={{ animationDelay: '0.45s' }}>
          <h2 id="signin-title" className="sr-only">
            Sign in to vote
          </h2>
          {notice === 'signed_out' ? (
            <p className="stage-note mb-4" role="status">
              You signed out. Sign in again whenever you are ready.
            </p>
          ) : null}
          {phase === 'before_open' ? (
            <p className="stage-note mb-4">
              Sign-in opens {opensAt ? `at ${opensAt}` : 'at a time the organisers will announce'}. Keep your invitation email: it
              has your password.
            </p>
          ) : null}
          {phase === 'closed' ? (
            <p className="stage-note mb-4">Sign-in has closed. Voting for this programme has finished. Thank you for taking part.</p>
          ) : null}

          <form className="space-y-4" onSubmit={submit} noValidate>
            <StageInput
              label="Email ID"
              icon={<UserRound className="size-[1.15rem]" />}
              name="username"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="Enter your email address"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              disabled={disabled}
            />
            <StageInput
              label="Password"
              icon={<LockKeyhole className="size-[1.15rem]" />}
              password
              name="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={disabled}
            />
            <div aria-live="polite">
              {formError ? (
                <p className="stage-note stage-note-danger" role="alert">
                  {formError.message}
                </p>
              ) : null}
            </div>
            <button type="submit" className="stage-cta" disabled={disabled || busy} aria-busy={busy || undefined}>
              {busy ? <LoaderCircle aria-hidden className="size-[1.1rem] animate-spin" /> : null}
              {busy ? 'Entering…' : open ? 'Enter the arena' : phase === 'before_open' ? 'Opens soon' : 'Sign-in closed'}
            </button>
          </form>

          <p className="stage-hint mt-4 text-balance text-center text-[0.85rem] text-[#b4bbdc]">Use the email and password from your invitation.</p>
          {programme.help_contact ? (
            <p className="stage-hint mt-1 text-balance text-center text-[0.85rem] text-[#b4bbdc]">Need help? Ask {programme.help_contact}.</p>
          ) : null}
        </section>
      </main>
      <Stage />
    </div>
  )
}
