'use client'

import { LogIn } from 'lucide-react'
import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { ConnectionBanner, PageColumn, ParticipantHeader } from './bits'
import { CoinRain } from './coin-rain'
import { useParticipant } from './provider'

export function ParticipantFrame({ children }: { children: React.ReactNode }) {
  const p = useParticipant()

  if (p.status === 'signed_out') {
    return (
      <main>
        <PageColumn className="pt-16">
          <section className="rounded-[var(--radius-hero)] border border-sand bg-paper p-6 shadow-[var(--shadow-card)]">
            <h1 className="type-display text-[1.9rem]">You are signed out</h1>
            <p className="mt-2 text-plum-soft" role="status">
              {p.signedOutMessage ?? 'Sign in again to continue.'}
            </p>
            <Link
              href="/"
              data-variant="primary"
              className="mt-6 inline-flex min-h-12 items-center gap-2 rounded-full bg-primary px-6 font-semibold text-white hover:bg-primary-deep"
            >
              <LogIn aria-hidden className="size-4" />
              Sign in
            </Link>
          </section>
        </PageColumn>
      </main>
    )
  }

  if (!p.snapshot) {
    return (
      <main aria-busy={p.status === 'loading'}>
        <PageColumn className="pt-4">
          {p.status === 'error' ? (
            <div className="pt-12">
              <Notice
                tone="danger"
                role="alert"
                title="We could not load the programme"
                action={<Button variant="secondary" onClick={() => void p.refresh()}>Try again</Button>}
              >
                Check your connection. Your votes are safe on the server.
              </Notice>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 py-3">
                <div className="skeleton size-10 rounded-[14px]" />
                <div className="skeleton h-5 w-40" />
              </div>
              <div className="skeleton mt-6 h-5 w-32" />
              <div className="skeleton mt-3 h-64 w-full rounded-[var(--radius-hero)]" />
              <div className="skeleton mt-8 h-20 w-full rounded-[var(--radius-card)]" />
              <div className="skeleton mt-3 h-20 w-full rounded-[var(--radius-card)]" />
            </>
          )}
        </PageColumn>
      </main>
    )
  }

  const s = p.snapshot
  return (
    <div data-accent={s.programme.accent} className="relative isolate min-h-dvh">
      {s.programme.accent === 'gold' ? <CoinRain /> : null}
      <PageColumn className="safe-top">
        <ParticipantHeader
          programme={s.programme}
          displayName={s.participant.display_name}
          userCode={s.participant.user_code}
          connection={p.connection}
          onSignOut={() => void p.signOut()}
        />
        <ConnectionBanner
          connection={p.connection}
          authoritative={p.authoritative}
          stale={p.stale}
          onRetry={() => void p.refresh()}
        />
      </PageColumn>
      {children}
    </div>
  )
}
