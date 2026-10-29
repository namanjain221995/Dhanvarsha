'use client'

import { LoaderCircle } from 'lucide-react'
import { useEffect, useSyncExternalStore } from 'react'
import { formatInZone, zoneAbbreviation } from '@/lib/time'
import type { Accent, PublicProgramme } from '@/lib/types'
import { OpeningCountdown } from './opening-countdown'
import type { LinkSignIn } from './sign-in'

/*
 * The gold look's sign-in: a night-blue arena with gold light, a ticket emblem, and a lit
 * stage at the foot of the page. The scene is decoration only (hidden from assistive
 * technology); the button keeps exactly the classic page's behaviour.
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

/**
 * The gold-ringed navy emblem with the TechSara mark (also the opening page's emblem). `src`
 * puts an uploaded logo inside the ring instead, kept within the inner circle at its own shape.
 */
export function BrandEmblem({ src }: { src?: string | null }) {
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
      {/* The TechSara mark (public/brand/techsara-mark.png, its triangle centred on the canvas), appearing after the ring draws. */}
      {src ? (
        <image className="stage-ticket" href={src} x="25" y="25" width="70" height="70" preserveAspectRatio="xMidYMid meet" />
      ) : (
        <image className="stage-ticket" href="/brand/techsara-mark.png" x="17" y="17" width="86" height="86" />
      )}
    </svg>
  )
}

/** Gold light sweeping down from the top corners, like stage curtains drawn in light. */
export function StageArcs() {
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

/** "Dhanvarsha 2026" is shown as the name with the year on its own line beneath it. */
export function splitTitle(name: string): { title: string; year: string | null } {
  const match = /^(.*\S)\s+((?:19|20)\d{2})$/.exec(name.trim())
  return match?.[1] && match[2] ? { title: match[1], year: match[2] } : { title: name, year: null }
}

function StageTitle({ name }: { name: string }) {
  const { title, year } = splitTitle(name)
  return (
    <h1 className="stage-title stage-rise mt-5" style={{ animationDelay: '0.25s', fontSize: titleSize(title) }}>
      {title}
      {year ? <span className="stage-title-year">{year}</span> : null}
    </h1>
  )
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

export function StageLoading({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <div data-scene="stage" className="stage-page relative isolate flex min-h-dvh flex-col">
      <StageArcs />
      <div className="mx-auto flex w-full max-w-[420px] flex-1 flex-col justify-center px-6 py-10 text-center" aria-busy={!error}>
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

export function StageSignIn({ programme, signIn }: { programme: PublicProgramme; signIn: LinkSignIn }) {
  const { link, busy, error, notice, enter, retry, reload } = signIn
  const tz = programme.timezone
  const phase = programme.login_phase
  const open = phase === 'open'
  const opensAt = programme.login_opens_at
    ? `${formatInZone(programme.login_opens_at, tz, { weekday: 'short', hour: 'numeric', minute: '2-digit' })} ${zoneAbbreviation(tz, programme.login_opens_at)}`
    : null

  return (
    <div data-accent="gold" data-scene="stage" className="stage-page relative isolate flex min-h-dvh flex-col">
      <StageArcs />
      {/* The page (app/page.tsx, app/enter/page.tsx) provides the main landmark. The column
          fills the space above the stage and centres its content in it. */}
      <div className="relative mx-auto flex w-full max-w-[420px] flex-1 flex-col justify-center px-6 pb-6 pt-[max(2.75rem,env(safe-area-inset-top))]">
        <header className="text-center">
          <div className="stage-emblem-wrap">
            <BrandEmblem />
          </div>
          <StageTitle name={programme.name} />
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
          {phase === 'before_open' ? (
            <p className="stage-note mb-4">
              Sign-in opens {opensAt ? `at ${opensAt}` : 'at a time the organisers will announce'}. Keep your invitation email: its
              button signs you in.
            </p>
          ) : null}
          {phase === 'closed' ? (
            <p className="stage-note mb-4">Sign-in has closed. Voting for this programme has finished. Thank you for taking part.</p>
          ) : null}

          {link.kind === 'ready' ? (
            <>
              <div aria-live="polite">
                {error ? (
                  <p className="stage-note stage-note-danger mb-4" role="alert">
                    {error}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                className="stage-cta"
                disabled={!open || busy}
                aria-busy={busy || undefined}
                aria-describedby="signin-hint"
                onClick={() => void enter()}
              >
                {busy ? <LoaderCircle aria-hidden className="size-[1.1rem] animate-spin" /> : null}
                {busy ? 'Entering…' : open ? 'Enter the arena' : phase === 'before_open' ? 'Opens soon' : 'Sign-in closed'}
              </button>
              <p id="signin-hint" className="stage-hint mt-4 text-balance text-center text-[0.85rem] text-[#b4bbdc]">
                This button signs you in on this device only.
              </p>
            </>
          ) : link.kind === 'checking' ? (
            <p role="status" className="stage-hint flex min-h-14 items-center justify-center gap-2 text-[0.92rem] text-[#b4bbdc]">
              <LoaderCircle aria-hidden className="size-[1.1rem] animate-spin" />
              One moment…
            </p>
          ) : link.kind === 'no_link' ? (
            <p role="status" className="stage-note text-balance text-center">
              {notice ?? 'To enter, open your invitation email on this phone and tap “Enter the arena”.'}
            </p>
          ) : (
            <div role="alert" className="stage-note stage-note-danger">
              <p>{link.message}</p>
              {link.kind === 'unreachable' ? (
                <button type="button" onClick={retry} className="stage-link mt-3">
                  Try again
                </button>
              ) : null}
            </div>
          )}

          {programme.help_contact ? (
            <p className={`stage-hint ${link.kind === 'ready' ? 'mt-1' : 'mt-4'} text-balance text-center text-[0.85rem] text-[#b4bbdc]`}>
              Need help? Ask {programme.help_contact}.
            </p>
          ) : null}
        </section>
      </div>
      <Stage />
    </div>
  )
}
