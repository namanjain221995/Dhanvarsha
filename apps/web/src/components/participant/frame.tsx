'use client'

import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { ConnectionBanner, PageColumn, ParticipantHeader } from './bits'
import { CoinRain } from './coin-rain'
import { StarField } from './star-field'
import { useParticipant } from './provider'

export function ParticipantFrame({ children }: { children: React.ReactNode }) {
  const p = useParticipant()

  if (p.status === 'signed_out') {
    // Voters come back only through the link in their invitation email, and a disabled
    // account cannot come back at all, so its page does not suggest it. Most server
    // messages already say how to come back; the line below is only added when one doesn't.
    const canReturn =
      p.signedOutCode !== 'ACCOUNT_DISABLED' &&
      p.signedOutReason !== 'disabled' &&
      !(p.signedOutMessage ?? '').includes('invitation email')
    return (
      <main>
        <PageColumn className="pt-16">
          <section className="rounded-[var(--radius-hero)] border border-sand bg-paper p-6 shadow-[var(--shadow-card)]">
            <h1 className="type-display text-[1.9rem]">You are signed out</h1>
            {p.signedOutMessage ? (
              <p className="mt-2 text-plum-soft" role="status">
                {p.signedOutMessage}
              </p>
            ) : null}
            {canReturn ? (
              <p className="mt-4 font-semibold text-plum">Open your invitation email on this device and tap the button to come back.</p>
            ) : null}
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
    <div data-accent={s.programme.accent} data-page="participant" className="relative isolate min-h-dvh">
      {s.programme.accent === 'gold' ? (
        <>
          <StarField />
          <CoinRain />
        </>
      ) : null}
      <PageColumn>
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
