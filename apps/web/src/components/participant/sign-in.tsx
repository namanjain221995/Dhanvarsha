'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { PasswordField, TextField } from '@/components/ui/field'
import { Notice } from '@/components/ui/misc'
import { ApiError, participantApi } from '@/lib/api'
import { formatInZone, localEquivalent, serverClock, zoneAbbreviation } from '@/lib/time'
import type { PublicProgramme } from '@/lib/types'
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

export type SignInForm = ReturnType<typeof useSignInForm>

/** Everything the sign-in pages share: the programme, the form state and the submit action. */
export function useSignInForm() {
  const router = useRouter()
  const params = useSearchParams()
  const { programme, error: loadError, reload } = usePublicProgramme()
  const [userId, setUserId] = useState(params.get('u') ?? '')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<{ message: string; code?: string } | null>(null)
  const [checkingSession, setCheckingSession] = useState(true)
  const notice = params.get('notice')

  // Already signed in on this browser: go straight to the programme.
  useEffect(() => {
    let cancelled = false
    participantApi<{ signed_in: boolean }>('/auth/session', { timeoutMs: 8000 })
      .then((result) => {
        if (cancelled) return
        if (result.signed_in) router.replace('/lobby')
        else setCheckingSession(false)
      })
      .catch(() => {
        if (!cancelled) setCheckingSession(false)
      })
    return () => {
      cancelled = true
    }
  }, [router])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy) return
    setFormError(null)
    if (!userId.trim() || !password) {
      setFormError({ message: 'Enter the email address and password from your invitation.' })
      return
    }
    setBusy(true)
    try {
      await participantApi('/auth/login', { method: 'POST', body: { user_id: userId.trim(), password } })
      clearVotingStorage()
      router.replace('/lobby')
    } catch (err) {
      setBusy(false)
      if (!(err instanceof ApiError)) {
        setFormError({ message: 'Something went wrong. Try again.' })
        return
      }
      if (err.code === 'LOGIN_NOT_OPEN' || err.code === 'LOGIN_CLOSED') void reload()
      if (err.isNetwork) {
        setFormError({ message: 'Could not reach the server. Check your connection and try again.' })
        return
      }
      setFormError({ message: err.message, code: err.code })
    }
  }

  return {
    programme,
    loadError,
    reload,
    userId,
    setUserId,
    password,
    setPassword,
    busy,
    formError,
    checkingSession,
    notice,
    submit,
  }
}

export function SignIn() {
  const form = useSignInForm()
  const { programme, loadError, reload, userId, setUserId, password, setPassword, busy, formError, checkingSession, notice, submit } = form
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
  if (programme.accent === 'gold') return <StageSignIn programme={programme} form={form} />

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
          {notice === 'signed_out' ? (
            <p className="mt-2 text-plum-soft">You signed out. Sign in again whenever you are ready.</p>
          ) : null}
          {phase === 'before_open' ? (
            <Notice className="mt-4" tone="info" title="Sign-in has not opened yet">
              It opens at {programme.login_opens_at ? `${formatInZone(programme.login_opens_at, tz, { hour: 'numeric', minute: '2-digit', weekday: 'short' })} ${zoneAbbreviation(tz, programme.login_opens_at)}` : 'a time the organisers will announce'}. Keep your invitation email: it has your password.
            </Notice>
          ) : null}
          {phase === 'closed' ? (
            <Notice className="mt-4" tone="warning" title="Sign-in has closed">
              Voting for this programme has finished. Thank you for taking part.
            </Notice>
          ) : null}

          <form className="mt-5 space-y-4" onSubmit={submit} noValidate>
            <TextField
              label="Email address"
              name="username"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              hint="Use the email address and password from your invitation email."
              disabled={!open || checkingSession}
            />
            <PasswordField
              label="Password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={!open || checkingSession}
            />
            <div aria-live="polite">
              {formError ? (
                <Notice tone="danger" role="alert">
                  {formError.message}
                </Notice>
              ) : null}
            </div>
            <Button type="submit" size="lg" block busy={busy} disabled={!open || checkingSession}>
              {busy ? 'Signing in…' : open ? 'Sign in' : phase === 'before_open' ? 'Sign-in opens soon' : 'Sign-in closed'}
            </Button>
          </form>

          <p className="mt-5 border-t border-sand pt-4 text-sm text-plum-soft">
            No invitation, or lost your password? Ask the organisers to send your invitation again.
          </p>
        </section>

        {programme.help_contact ? (
          <p className="mt-6 text-sm text-plum-soft">Need help? Ask {programme.help_contact}.</p>
        ) : null}
      </PageColumn>
    </div>
  )
}
