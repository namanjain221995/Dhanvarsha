'use client'

import { LogIn } from 'lucide-react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { PasswordField, TextField } from '@/components/ui/field'
import { Notice } from '@/components/ui/misc'
import { adminApi, ApiError, setCsrfToken } from '@/lib/api'
import { describeError } from '@/lib/admin/errors'
import type { AdminMe } from '@/lib/admin/types'

/** Only same-console paths are allowed as a return address. */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/admin') || raw.startsWith('//') || raw.startsWith('/admin/login')) return '/admin'
  return raw
}

export function AdminLoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const next = safeNext(params.get('next'))
  const signedOut = params.get('signed_out') === '1'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  // An organiser who is already signed in on this browser goes straight to the console.
  useEffect(() => {
    let cancelled = false
    adminApi<{ signed_in: boolean }>('/admin/auth/session', { timeoutMs: 8000 })
      .then((result) => {
        if (!cancelled && result.signed_in && !signedOut) router.replace(next)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [router, next, signedOut])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError(!email.trim() ? 'Enter your organiser email address.' : 'Enter your password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await adminApi('/admin/auth/login', { method: 'POST', body: { email: email.trim(), password } })
      const me = await adminApi<AdminMe>('/admin/me')
      setCsrfToken('admin', me.csrf_token)
      router.replace(next)
    } catch (failure) {
      setBusy(false)
      if (failure instanceof ApiError && failure.code === 'INVALID_CREDENTIALS') {
        setError('That email or password is not right. Check both and try again.')
        setPassword('')
        passwordRef.current?.focus()
      } else if (failure instanceof ApiError && failure.code === 'RATE_LIMITED') {
        const seconds = Number(failure.details?.retry_after_seconds ?? 0)
        const minutes = Math.max(1, Math.ceil(seconds / 60))
        setError(`Too many unsuccessful attempts. Wait about ${minutes} minute${minutes === 1 ? '' : 's'} and try again.`)
      } else {
        setError(describeError(failure))
      }
    }
  }

  return (
    <main className="flex min-h-dvh items-start justify-center px-4 pb-16 pt-[12vh]">
      <div className="w-full max-w-[26rem]">
        <p className="text-sm font-semibold text-plum-soft">StageVote</p>
        <h1 className="type-display mt-1 text-[2.4rem] text-plum">Organiser sign-in</h1>
        <p className="mt-2 text-plum-soft">Run the live programme, manage participants and publish results.</p>
        <div aria-hidden className="mt-6 h-px bg-gradient-to-r from-gold/70 via-gold/30 to-transparent" />

        <form
          noValidate
          onSubmit={(event) => void submit(event)}
          className="mt-6 rounded-[var(--radius-card)] border border-sand bg-paper p-5 shadow-[var(--shadow-card)] sm:p-6"
        >
          <div aria-live="polite" className="empty:hidden [&:not(:empty)]:mb-5">
            {signedOut && !error ? <Notice tone="success">You signed out of the organiser console.</Notice> : null}
            {error ? (
              <Notice tone="danger" role="alert">
                {error}
              </Notice>
            ) : null}
          </div>
          <TextField
            label="Email"
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <PasswordField
            ref={passwordRef}
            label="Password"
            name="password"
            autoComplete="current-password"
            className="mt-4"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
          <Button type="submit" size="lg" block busy={busy} className="mt-6">
            {busy ? null : <LogIn aria-hidden className="size-5" />}
            Sign in
          </Button>
        </form>
        <p className="mt-5 text-sm text-plum-soft">
          Participants sign in on the programme page, not here.
        </p>
      </div>
    </main>
  )
}
