'use client'

import { ArrowRight, BarChart3, ChevronRight, Lock } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import type { EventSummary, Snapshot } from '@/lib/types'
import { type ChipKind, Countdown, EventLogo, PageColumn, StatusChip, timeLabel, titleSize } from './bits'
import { useParticipant } from './provider'
import { navigateWithTransition, transitionName } from './transition'

function votedCount(event: EventSummary): number {
  return event.ballots.filter((b) => b.voted).length
}

function chipFor(event: EventSummary): { kind: ChipKind; label?: string } {
  const voted = votedCount(event)
  const total = event.ballots.length
  if (event.status === 'upcoming') return { kind: 'upcoming' }
  if (total > 0 && voted === total) return { kind: 'recorded' }
  if (event.status === 'live') {
    if (voted > 0) return { kind: 'partial', label: `${voted} of ${total} voted` }
    return { kind: 'live' }
  }
  return { kind: 'closed' }
}

function useTransitionLink() {
  const router = useRouter()
  return (href: string) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    event.preventDefault()
    navigateWithTransition(() => router.push(href))
  }
}

function LiveFeature({ event, snapshot }: { event: EventSummary; snapshot: Snapshot }) {
  const tz = snapshot.programme.timezone
  const voted = votedCount(event)
  const total = event.ballots.length
  const allVoted = total > 0 && voted === total
  const onClick = useTransitionLink()
  const href = `/event/${event.id}`
  return (
    <a
      href={href}
      onClick={onClick(href)}
      className="enter-up block rounded-[var(--radius-hero)] border border-primary/25 bg-paper p-5 shadow-[var(--shadow-lift)] outline-offset-4 transition-transform duration-150 active:scale-[0.99] sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusChip kind={allVoted ? 'recorded' : 'live'} label={allVoted ? 'Vote recorded' : 'Live now'} />
        <span className="text-sm font-semibold text-plum-soft">
          Round {event.round} of {snapshot.event_count}
        </span>
      </div>
      <div className="mt-5 flex items-center gap-4">
        <EventLogo src={event.logo_url} name={event.name} size={76} transitionId={transitionName('logo', event.id)} />
        <h2
          className={`type-display min-w-0 text-plum [overflow-wrap:anywhere] ${titleSize(event.name, 'feature')}`}
          style={{ viewTransitionName: transitionName('title', event.id) }}
        >
          {event.name}
        </h2>
      </div>
      {event.voting_deadline_at ? (
        <dl className="mt-6 grid grid-cols-2 gap-4 border-t border-sand pt-4">
          <div>
            <dt className="text-sm text-plum-soft">Voting closes</dt>
            <dd className="tabular mt-0.5 font-semibold">{timeLabel(event.voting_deadline_at, tz)}</dd>
          </div>
          <div>
            <dt className="text-sm text-plum-soft">Time left</dt>
            <dd className="mt-0.5 font-semibold">
              <Countdown deadline={event.voting_deadline_at} />
            </dd>
          </div>
        </dl>
      ) : null}
      {total > 1 ? (
        <p className="mt-4 text-sm text-plum-soft">
          {total} ballots in this round. {voted > 0 ? `You have voted on ${voted}.` : ''}
        </p>
      ) : null}
      <span
        className={`mt-5 flex min-h-13 w-full items-center justify-center gap-2 rounded-full text-base font-semibold ${
          allVoted ? 'border border-plum-faint/60 text-plum' : 'bg-primary text-white'
        }`}
      >
        {allVoted ? 'View your vote' : voted > 0 ? 'Continue voting' : 'Vote now'}
        <ArrowRight aria-hidden className="size-4" />
      </span>
    </a>
  )
}

function WaitingCard({ snapshot }: { snapshot: Snapshot }) {
  const next = snapshot.events.find((e) => e.id === snapshot.next_event_id)
  const tz = snapshot.programme.timezone
  return (
    <section className="enter-up rounded-[var(--radius-hero)] border border-sand bg-paper p-6 shadow-[var(--shadow-card)]">
      <h2 className="type-display text-[1.9rem]">The next round opens soon</h2>
      <p className="mt-2 text-plum-soft">
        Keep this page open. When the host opens a round, it appears here by itself.
      </p>
      {next ? (
        <div className="mt-5 flex items-center gap-3 border-t border-sand pt-4">
          <EventLogo src={next.logo_url} name={next.name} size={44} locked={!next.name} />
          <div className="min-w-0">
            <p className="font-semibold">
              Next: Round {next.round}
              {next.name ? `, ${next.name}` : ''}
            </p>
            {next.planned_start_at ? (
              <p className="tabular text-sm text-plum-soft">Planned for {timeLabel(next.planned_start_at, tz)}</p>
            ) : (
              <p className="text-sm text-plum-soft">Start time to be announced</p>
            )}
          </div>
        </div>
      ) : null}
    </section>
  )
}

function ThankYou({ snapshot }: { snapshot: Snapshot }) {
  const tz = snapshot.programme.timezone
  const closed = snapshot.phase === 'access_closed'
  return (
    <section className="enter-up rounded-[var(--radius-hero)] border border-sand bg-paper p-6 shadow-[var(--shadow-card)]">
      <div aria-hidden className="h-[3px] w-14 rounded-full bg-gold" />
      <h2 className="type-display mt-5 text-[2.2rem]">Thank you for voting</h2>
      <p className="mt-2 text-plum-soft">
        {closed ? 'The programme has closed.' : 'Every round has finished.'}{' '}
        {snapshot.receipts.length
          ? `You voted on ${snapshot.receipts.length} ballot${snapshot.receipts.length === 1 ? '' : 's'}. Your votes are stored and final.`
          : 'No votes were recorded for your account.'}
      </p>
      {snapshot.receipts.length ? (
        <ul className="mt-5 divide-y divide-sand border-t border-sand">
          {snapshot.receipts.map((r) => (
            <li key={r.ballot_id} className="flex items-baseline justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm text-plum-soft">
                  {r.event_name}, {r.ballot_title}
                </p>
                <p className="truncate font-semibold">{r.nominee_name}</p>
              </div>
              <div className="shrink-0 text-right">
                <p className="tabular text-sm font-semibold">{r.receipt_code}</p>
                <p className="tabular text-xs text-plum-soft">{timeLabel(r.accepted_at, tz)}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function EventRow({ event, isFeatured, tz }: { event: EventSummary; isFeatured: boolean; tz: string }) {
  const chip = chipFor(event)
  const released = event.status !== 'upcoming'
  const onClick = useTransitionLink()
  const href = `/event/${event.id}`
  const named = released || !!event.name
  const body = (
    <>
      <span className="type-display tabular w-7 shrink-0 text-center text-[1.35rem] text-plum-faint" aria-hidden>
        {event.round}
      </span>
      <EventLogo
        src={named ? event.logo_url : null}
        name={event.name}
        size={released ? 46 : 40}
        locked={!named}
        transitionId={!isFeatured && released ? transitionName('logo', event.id) : undefined}
      />
      <span className="min-w-0 flex-1">
        <span className="sr-only">Round {event.round}: </span>
        {event.name ? (
          <span
            className="line-clamp-2 font-semibold leading-snug text-plum [overflow-wrap:anywhere]"
            style={!isFeatured && released ? { viewTransitionName: transitionName('title', event.id) } : undefined}
          >
            {event.name}
          </span>
        ) : (
          <span className="block py-1.5">
            <span className="sr-only">Details unlock when the round opens.</span>
            <span aria-hidden className="placeholder-bar block h-3 w-40 max-w-full" />
          </span>
        )}
        <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusChip kind={chip.kind} label={chip.label} />
          {event.results_published ? (
            <span className="inline-flex items-center gap-1 text-sm font-semibold text-primary">
              <BarChart3 aria-hidden className="size-3.5" /> Results
            </span>
          ) : event.status === 'upcoming' && event.planned_start_at ? (
            <span className="tabular text-sm text-plum-soft">Planned for {timeLabel(event.planned_start_at, tz)}</span>
          ) : null}
        </span>
      </span>
      {released ? <ChevronRight aria-hidden className="size-5 shrink-0 text-plum-faint" /> : null}
    </>
  )
  const base = 'flex items-center gap-3 rounded-[var(--radius-card)] border px-3 sm:px-4'
  if (!released && !event.name) {
    return (
      <div className={`${base} min-h-14 border-sand/70 bg-paper/50 py-2`}>
        <span className="type-display tabular w-7 shrink-0 text-center text-[1.35rem] text-plum-faint" aria-hidden>
          {event.round}
        </span>
        <EventLogo src={null} name={null} size={36} locked />
        <span className="min-w-0 flex-1">
          <span className="sr-only">Round {event.round}: details unlock when the round opens.</span>
          <span aria-hidden className="placeholder-bar block h-3 w-full max-w-40" />
          {event.planned_start_at ? (
            <span className="tabular mt-1 block text-sm text-plum-soft">Planned for {timeLabel(event.planned_start_at, tz)}</span>
          ) : null}
        </span>
        <StatusChip kind="upcoming" />
      </div>
    )
  }
  if (!released) {
    return <div className={`${base} min-h-16 border-sand/70 bg-paper/50 py-2.5`}>{body}</div>
  }
  return (
    <a
      href={href}
      onClick={onClick(href)}
      className={`${base} min-h-[4.75rem] bg-paper py-3 transition-colors duration-150 hover:border-plum-faint/60 ${
        event.status === 'live' ? 'border-primary/30' : 'border-sand'
      }`}
    >
      {body}
    </a>
  )
}

export function Lobby() {
  const { snapshot } = useParticipant()
  const [announcement, setAnnouncement] = useState('')
  const previousLive = useRef<string | null | undefined>(undefined)

  useEffect(() => {
    if (!snapshot) return
    const live = snapshot.events.find((e) => e.id === snapshot.live_event_id)
    if (previousLive.current !== undefined && snapshot.live_event_id !== previousLive.current) {
      setAnnouncement(live ? `Round ${live.round} is live: ${live.name}.` : 'Voting for the round has closed.')
    }
    previousLive.current = snapshot.live_event_id
  }, [snapshot])

  if (!snapshot) return null
  const live = snapshot.events.find((e) => e.id === snapshot.live_event_id) ?? null
  const tz = snapshot.programme.timezone

  return (
    <main className="pb-16">
      <PageColumn>
        <p className="sr-only" aria-live="polite">
          {announcement}
        </p>
        <div className="mt-4">
          {snapshot.phase === 'live' && live ? (
            <LiveFeature key={live.id} event={live} snapshot={snapshot} />
          ) : snapshot.phase === 'waiting' ? (
            <WaitingCard snapshot={snapshot} />
          ) : (
            <ThankYou snapshot={snapshot} />
          )}
        </div>

        {snapshot.programme.instructions && snapshot.phase !== 'completed' ? (
          <p className="mt-5 text-[0.95rem] leading-relaxed text-plum-soft">{snapshot.programme.instructions}</p>
        ) : null}

        <section aria-labelledby="rounds-heading" className="mt-10">
          <div className="flex items-baseline justify-between gap-3">
            <h2 id="rounds-heading" className="type-display text-[1.45rem]">
              All rounds
            </h2>
            <span className="text-sm text-plum-soft">
              {snapshot.receipts.length} vote{snapshot.receipts.length === 1 ? '' : 's'} cast
            </span>
          </div>
          <ol className="mt-3 space-y-2.5">
            {snapshot.events.map((event) => (
              <li key={event.id}>
                <EventRow event={event} isFeatured={event.id === live?.id} tz={tz} />
              </li>
            ))}
          </ol>
          {snapshot.events.some((e) => e.status === 'upcoming') ? (
            <p className="mt-4 flex items-center gap-2 text-sm text-plum-soft">
              <Lock aria-hidden className="size-4" />
              Upcoming rounds unlock when the host opens them.
            </p>
          ) : null}
        </section>
        <p className="mt-10 text-center text-xs text-plum-soft">Times shown in {tz.replace('_', ' ')}.</p>
      </PageColumn>
    </main>
  )
}
