'use client'

import { LoaderCircle } from 'lucide-react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { ApiError, participantApi } from '@/lib/api'
import { formatInZone, localEquivalent, serverClock, zoneAbbreviation } from '@/lib/time'
import type { LinkCheck, LinkEntry, PublicProgramme } from '@/lib/types'
import { PageColumn } from './bits'
import { OpeningCountdown } from './opening-countdown'
import { ProgrammeLogo } from './programme-logo'
import { StageLoading, StageSignIn, useRememberedAccent } from './stage-sign-in'
import { clearVotingStorage } from './use-voting'

export function usePublicProgramme() {
  const [programme, setProgramme] = useState<PublicProgramme | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    const started = Date.now()
    try {
      const data = await participantApi<PublicProgramme>('/public/programme', { timeoutMs: 10_000 })
      serverClock.observe(data.server_time, started, Date.now())
      setProgramme(data)
      setError(null)
      return data
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the server.')
      return null
    }
  }, [])
  useEffect(() => {
    // Initial load from an external system (the API); state updates happen after it resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])
  return { programme, error, reload: load }
}

export function ProgrammeMasthead({ programme, compact = false }: { programme: PublicProgramme; compact?: boolean }) {
  const tz = programme.timezone
  return (
    <div className={compact ? 'pt-8' : 'pt-10 sm:pt-14'}>
      {/* Logo and name side by side, as one lockup. */}
      <div className="flex items-center gap-4 sm:gap-5">
        <ProgrammeLogo src={programme.logo_url} name={programme.name} height={compact ? 48 : 64} />
        <h1 className={`type-display min-w-0 break-words text-plum ${compact ? 'text-[2.1rem]' : 'text-[2.4rem] sm:text-[3.3rem]'}`}>
          {programme.name}
        </h1>
      </div>
      {!compact && programme.description ? (
        <p className="mt-3 max-w-[46ch] text-[1.02rem] leading-relaxed text-plum-soft">{programme.description}</p>
      ) : null}
      {programme.login_opens_at ? (
        <p className="mt-4 text-[0.95rem] text-plum-soft">
          {programme.login_phase === 'before_open' ? 'Sign-in opens ' : 'Opened '}
          <span className="font-semibold text-plum">
            {formatInZone(programme.login_opens_at, tz, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}{' '}
            {zoneAbbreviation(tz, programme.login_opens_at)}
          </span>
          {localEquivalent(programme.login_opens_at, tz) ? (
            <span className="block text-sm">({localEquivalent(programme.login_opens_at, tz)})</span>
          ) : null}
        </p>
      ) : null}
      <div aria-hidden className="mt-6 h-[3px] w-14 rounded-full bg-gold" />
    </div>
  )
}

/*
 * Voters sign in with the personal link in their invitation email, /enter#token=…. The token
 * sits in the fragment, so it never reaches a server, a log or a Referer header. The page reads
 * it once, keeps it in this tab's storage (so a reload still works) and removes it from the
 * address bar, so the browser cannot copy or share a working link. Opening the page only checks
 * the link: it is used only when the voter taps the button, so email scanners that open links
 * cannot take it. The first browser that taps the button keeps the link.
 */

/** This tab's copy of the token. Not an 'sv:' key: clearVotingStorage() removes those. */
const LINK_TOKEN_KEY = 'svlink:token'
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{43}$/
/** Preview and test emails link to this token instead of a working one. */
const EXAMPLE_TOKEN = 'example'

/** Shown word for word. invalid and inUse are the API's LINK_INVALID and LINK_IN_USE messages. */
const MESSAGES = {
  invalid: 'This sign-in link is not valid any more. Open the newest invitation email, or ask the help desk to send you a new one.',
  inUse:
    'This link is already in use on another phone or browser. If that was you in another app (for example inside your email app), open the link there. Otherwise ask the help desk to send you a new invitation.',
  disabled: 'This account has been disabled. Contact the organisers for help.',
  example: 'This is an example link from a preview email. It does not sign anyone in.',
  signedOut: 'You signed out. To come back, open your invitation email on this device and tap the button.',
  network: 'Could not reach the server. Check your connection and try again.',
  rateLimited: 'Too many attempts from this network. Wait a minute and try again.',
}

/** A tap refused for one of these reasons is refused again on every tap, so the button goes away. */
const FINAL_REFUSALS = new Set(['LINK_IN_USE', 'LINK_INVALID', 'ACCOUNT_DISABLED'])

/** The API refuses a token far too long to be a link (422) instead of answering "invalid". */
function isMalformedLink(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422
}

export type LinkState =
  /** Looking at the link (or, without one, at the session). */
  | { kind: 'checking' }
  /** The link can sign in on this device: the button is shown. */
  | { kind: 'ready' }
  /** No link on this page or in this tab, and not signed in. */
  | { kind: 'no_link' }
  /** The link cannot sign anyone in here: in use elsewhere, replaced, disabled, or an example. */
  | { kind: 'refused'; message: string }
  /** The link could not be checked; "Try again" checks it again. */
  | { kind: 'unreachable'; message: string }

/** The token in the address: the emailed link's fragment (#token=), or ?token= as a fallback. */
function tokenInAddress(): string | null {
  const fromFragment = new URLSearchParams(window.location.hash.slice(1)).get('token')?.trim()
  const fromQuery = new URLSearchParams(window.location.search).get('token')?.trim()
  return fromFragment || fromQuery || null
}

function storedToken(): string | null {
  try {
    return window.sessionStorage.getItem(LINK_TOKEN_KEY)
  } catch {
    return null
  }
}

function storeToken(token: string | null) {
  try {
    if (token) window.sessionStorage.setItem(LINK_TOKEN_KEY, token)
    else window.sessionStorage.removeItem(LINK_TOKEN_KEY)
  } catch {
    /* storage unavailable: the page keeps the token in memory */
  }
}

function failureMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong. Try again.'
  if (error.isNetwork) return MESSAGES.network
  if (error.code === 'RATE_LIMITED') return MESSAGES.rateLimited
  return error.message
}

// Link checks still on their way. A check may set this browser's device cookie, so a tap waits
// for every check to finish first: otherwise a late reply could replace the cookie the tap
// just tied the link to, and lock the owner out of their own link.
const pendingChecks = new Set<Promise<unknown>>()

function trackCheck<T>(request: Promise<T>): Promise<T> {
  pendingChecks.add(request)
  void request.catch(() => undefined).finally(() => pendingChecks.delete(request))
  return request
}

/** What this page can offer. Read-only: it never uses the link and never starts a session. */
async function inspect(token: string | null): Promise<LinkState | { kind: 'signed_in' }> {
  if (!token) {
    try {
      const session = await participantApi<{ signed_in: boolean }>('/auth/session', { timeoutMs: 8000 })
      return session.signed_in ? { kind: 'signed_in' } : { kind: 'no_link' }
    } catch {
      return { kind: 'no_link' }
    }
  }
  if (!TOKEN_SHAPE.test(token)) {
    return { kind: 'refused', message: token === EXAMPLE_TOKEN ? MESSAGES.example : MESSAGES.invalid }
  }
  try {
    const { state } = await trackCheck(
      participantApi<LinkCheck>('/auth/link/check', { method: 'POST', body: { token }, timeoutMs: 10_000 }),
    )
    if (state === 'signed_in') return { kind: 'signed_in' }
    if (state === 'ready') return { kind: 'ready' }
    if (state === 'in_use') return { kind: 'refused', message: MESSAGES.inUse }
    if (state === 'disabled') return { kind: 'refused', message: MESSAGES.disabled }
    return { kind: 'refused', message: MESSAGES.invalid }
  } catch (error) {
    if (isMalformedLink(error)) return { kind: 'refused', message: MESSAGES.invalid }
    return { kind: 'unreachable', message: failureMessage(error) }
  }
}

export type LinkSignIn = ReturnType<typeof useLinkSignIn>

/** Everything the sign-in pages share: the programme, what the link allows here, and the button's action. */
export function useLinkSignIn() {
  const router = useRouter()
  const params = useSearchParams()
  const { programme, error: loadError, reload } = usePublicProgramme()
  const [link, setLink] = useState<LinkState>({ kind: 'checking' })
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const token = useRef<string | null>(null)
  const notice = params.get('notice') === 'signed_out' ? MESSAGES.signedOut : null

  useEffect(() => {
    let cancelled = false
    // Strict Mode runs this twice. The first run takes the token out of the address; the
    // second finds it in this tab's storage, or in memory when storage is unavailable.
    const fromAddress = tokenInAddress()
    if (fromAddress) {
      storeToken(fromAddress)
      // The Next.js router sees this replaceState and keeps its own copy of the address in step.
      window.history.replaceState(null, '', window.location.pathname)
    }
    const current = fromAddress ?? storedToken() ?? token.current
    token.current = current
    void inspect(current).then(async (outcome) => {
      if (cancelled) return
      if (outcome.kind === 'refused') {
        // A link that cannot sign in here is not kept for later visits.
        storeToken(null)
        if (!fromAddress) {
          // It was remembered from an earlier visit in this tab, not opened just now: forget it
          // quietly and treat this visit as one without a link (a signed-in voter goes to the lobby).
          token.current = null
          outcome = await inspect(null)
          if (cancelled) return
        }
      }
      if (outcome.kind !== 'signed_in') {
        setLink(outcome)
        return
      }
      // Already signed in here. Arriving through the link counts as a fresh sign-in; a plain
      // visit to the page keeps the drafts and pending votes of the session that is open.
      if (current) {
        storeToken(null)
        clearVotingStorage()
      }
      router.replace('/lobby')
    })
    // A newer link opened in this tab changes only the fragment, which does not reload the page.
    const onHashChange = () => {
      if (!tokenInAddress()) return
      setError(null)
      setLink({ kind: 'checking' })
      setAttempt((n) => n + 1)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      cancelled = true
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [router, attempt])

  /** The button. Only a tap uses the link; the first device that taps keeps it. */
  const enter = async () => {
    const current = token.current
    if (busy || !current) return
    setBusy(true)
    setError(null)
    try {
      await Promise.allSettled([...pendingChecks])
      await participantApi<LinkEntry>('/auth/link', { method: 'POST', body: { token: current } })
      storeToken(null)
      clearVotingStorage()
      router.replace('/lobby')
    } catch (err) {
      setBusy(false)
      if (err instanceof ApiError && (err.code === 'LOGIN_NOT_OPEN' || err.code === 'LOGIN_CLOSED')) void reload()
      if (err instanceof ApiError && FINAL_REFUSALS.has(err.code)) {
        storeToken(null)
        setLink({ kind: 'refused', message: err.message })
      }
      else if (isMalformedLink(err)) setLink({ kind: 'refused', message: MESSAGES.invalid })
      else setError(failureMessage(err))
    }
  }

  /** "Try again" after the link could not be checked. */
  const retry = () => {
    setError(null)
    setLink({ kind: 'checking' })
    setAttempt((n) => n + 1)
  }

  return { programme, loadError, reload, link, busy, error, notice, enter, retry }
}

export function SignIn() {
  const signIn = useLinkSignIn()
  const { programme, loadError, reload, link, busy, error, notice, enter, retry } = signIn
  const rememberedAccent = useRememberedAccent(programme?.accent)

  if (!programme && rememberedAccent === 'gold') return <StageLoading error={loadError} onRetry={() => void reload()} />
  if (!programme) {
    return (
      <PageColumn className="pb-16">
        {loadError ? (
          <div className="pt-16">
            <Notice tone="danger" title="We could not load the programme" role="alert" action={<Button variant="secondary" onClick={() => void reload()}>Try again</Button>}>
              {loadError}
            </Notice>
          </div>
        ) : (
          <div aria-busy className="pt-14">
            <div className="skeleton size-[60px] rounded-[14px]" />
            <div className="skeleton mt-6 h-12 w-4/5" />
            <div className="skeleton mt-3 h-5 w-3/5" />
            <div className="skeleton mt-10 h-64 w-full rounded-[var(--radius-card)]" />
          </div>
        )}
      </PageColumn>
    )
  }

  // The gold look signs people in on its own stage.
  if (programme.accent === 'gold') return <StageSignIn programme={programme} signIn={signIn} />

  const tz = programme.timezone
  const phase = programme.login_phase
  const open = phase === 'open'

  return (
    <div data-accent={programme.accent} className="relative isolate min-h-dvh">
      <PageColumn className="pb-16">
        <ProgrammeMasthead programme={programme} />

        {phase === 'before_open' && programme.countdown_enabled && programme.login_opens_at ? (
          <OpeningCountdown opensAt={programme.login_opens_at} onReached={() => void reload()} />
        ) : null}

        <section
          aria-labelledby="signin-title"
          className="mt-8 rounded-[var(--radius-hero)] border border-sand bg-paper p-5 shadow-[var(--shadow-card)] sm:p-6"
        >
          <h2 id="signin-title" className="type-display text-[1.7rem]">
            Sign in to vote
          </h2>
          {phase === 'before_open' ? (
            <Notice className="mt-4" tone="info" title="Sign-in has not opened yet">
              It opens at {programme.login_opens_at ? `${formatInZone(programme.login_opens_at, tz, { hour: 'numeric', minute: '2-digit', weekday: 'short' })} ${zoneAbbreviation(tz, programme.login_opens_at)}` : 'a time the organisers will announce'}. Keep your invitation email: its button signs you in.
            </Notice>
          ) : null}
          {phase === 'closed' ? (
            <Notice className="mt-4" tone="warning" title="Sign-in has closed">
              Voting for this programme has finished. Thank you for taking part.
            </Notice>
          ) : null}

          <div className="mt-5 space-y-4">
            {link.kind === 'ready' ? (
              <>
                <div aria-live="polite" className="empty:hidden">
                  {error ? (
                    <Notice tone="danger" role="alert">
                      {error}
                    </Notice>
                  ) : null}
                </div>
                <Button type="button" size="lg" block busy={busy} disabled={!open} aria-describedby="signin-hint" onClick={() => void enter()}>
                  {busy ? 'Signing in…' : open ? 'Sign in' : phase === 'before_open' ? 'Sign-in opens soon' : 'Sign-in closed'}
                </Button>
                <p id="signin-hint" className="text-sm text-plum-soft">
                  This button signs you in on this device only.
                </p>
              </>
            ) : link.kind === 'checking' ? (
              <p role="status" className="flex min-h-13 items-center gap-2 text-plum-soft">
                <LoaderCircle aria-hidden className="size-4 animate-spin" />
                One moment…
              </p>
            ) : link.kind === 'no_link' ? (
              <p role="status" className="text-plum-soft">
                {notice ?? 'To sign in, open your invitation email on this device and tap the button in it.'}
              </p>
            ) : (
              <Notice
                tone="danger"
                role="alert"
                action={link.kind === 'unreachable' ? <Button variant="secondary" onClick={retry}>Try again</Button> : undefined}
              >
                {link.message}
              </Notice>
            )}
          </div>

          <p className="mt-5 border-t border-sand pt-4 text-sm text-plum-soft">
            No invitation, or the link stopped working? Ask the organisers to send your invitation again.
          </p>
        </section>

        {programme.help_contact ? (
          <p className="mt-6 text-sm text-plum-soft">Need help? Ask {programme.help_contact}.</p>
        ) : null}
      </PageColumn>
    </div>
  )
}
