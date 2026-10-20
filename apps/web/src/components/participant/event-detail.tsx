'use client'

import { ArrowLeft, ArrowRight, Check, CircleSlash, LoaderCircle, Sparkles, UsersRound } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Avatar, Notice } from '@/components/ui/misc'
import { ApiError, participantApi } from '@/lib/api'
import { serverClock, useServerNow } from '@/lib/time'
import type { BallotDetail, BallotResult, EventDetail, Nominee } from '@/lib/types'
import { Countdown, EventLogo, PageColumn, StatusChip, timeLabel, titleSize } from './bits'
import { Confetti } from './confetti'
import { useParticipant } from './provider'
import { ReceiptStub } from './receipt'
import { navigateWithTransition, notifyPageReady, transitionName } from './transition'
import { type BallotState, useVoting } from './use-voting'

function NomineeOption({
  ballot,
  nominee,
  checked,
  disabled,
  onSelect,
}: {
  ballot: BallotDetail
  nominee: Nominee
  checked: boolean
  disabled: boolean
  onSelect: () => void
}) {
  const id = `n-${ballot.id}-${nominee.id}`
  const members = ballot.kind === 'team' ? (nominee.members ?? []) : []
  const described = Boolean(nominee.affiliation || nominee.description || members.length)
  return (
    <label
      htmlFor={id}
      className={`group relative flex min-h-[4.25rem] cursor-pointer items-center gap-3.5 rounded-[1.25rem] border-2 bg-paper px-3.5 py-3 transition-[border-color,background-color,box-shadow] duration-150 has-[:focus-visible]:shadow-[0_0_0_3px_var(--primary-ring)] ${
        checked ? 'border-primary bg-primary-wash/60' : 'border-sand hover:border-plum-faint/50'
      } ${disabled ? 'cursor-not-allowed opacity-70' : ''}`}
    >
      <input
        id={id}
        type="radio"
        name={`ballot-${ballot.id}`}
        value={nominee.id}
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="peer sr-only"
        aria-describedby={described ? `${id}-desc` : undefined}
      />
      <Avatar src={nominee.photo_url} name={nominee.name} size={52} rounded={ballot.kind === 'team' ? 'xl' : 'full'} />
      <span className="min-w-0 flex-1">
        <span className="block font-semibold leading-snug text-plum [overflow-wrap:anywhere]">{nominee.name}</span>
        {described ? (
          <span id={`${id}-desc`} className="mt-0.5 block text-sm leading-snug text-plum-soft">
            {nominee.affiliation ? <span className="block">{nominee.affiliation}</span> : null}
            {members.length ? <TeamMembers members={members} /> : null}
            {nominee.description ? <span className="line-clamp-2 block">{nominee.description}</span> : null}
          </span>
        ) : null}
      </span>
      <span
        aria-hidden
        className={`flex size-7 shrink-0 items-center justify-center rounded-full border-2 transition-colors duration-150 ${
          checked ? 'border-primary bg-primary text-white' : 'border-plum-faint/70 bg-paper text-transparent'
        }`}
      >
        <Check className="size-4" strokeWidth={3} />
      </span>
    </label>
  )
}

/** The people in a team, so voters know who they are voting for. */
function TeamMembers({ members, className = '' }: { members: string[]; className?: string }) {
  return (
    <span className={`flex items-start gap-1.5 text-plum ${className}`}>
      <UsersRound aria-hidden className="mt-[0.2rem] size-3.5 shrink-0 text-plum-soft" />
      <span>
        <span className="sr-only">Team members: </span>
        {members.join(', ')}
      </span>
    </span>
  )
}

function ResultsView({ result }: { result: BallotResult }) {
  const leaders = result.nominees.filter((n) => result.leaders.includes(n.nominee_id))
  return (
    <div className="rounded-[var(--radius-card)] border border-sand bg-paper p-4">
      <p className="font-semibold">{result.title}: results</p>
      <p className="mt-0.5 text-sm text-plum-soft">
        {result.total_votes} vote{result.total_votes === 1 ? '' : 's'} counted.{' '}
        {result.tie
          ? `Tie between ${leaders.map((l) => l.name).join(' and ')}.`
          : leaders[0]
            ? `Most votes: ${leaders[0].name}.`
            : 'No votes were cast.'}
      </p>
      <ul className="mt-3 space-y-2.5">
        {result.nominees.map((n) => (
          <li key={n.nominee_id}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className={`min-w-0 truncate ${n.leading ? 'font-semibold' : ''}`}>{n.name}</span>
              <span className="tabular shrink-0 text-plum-soft">
                {n.votes} ({n.percent}%)
              </span>
            </div>
            <div aria-hidden className="mt-1 h-2 overflow-hidden rounded-full bg-plum/[0.07]">
              <div className={`h-full rounded-full ${n.leading ? 'bg-primary' : 'bg-plum-faint/60'}`} style={{ width: `${n.percent}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function BallotSection({
  ballot,
  state,
  votable,
  showTitle,
  timezone,
  round,
  onSelect,
  headingRef,
}: {
  ballot: BallotDetail
  state: BallotState | undefined
  votable: boolean
  showTitle: boolean
  timezone: string
  round: number
  onSelect: (nomineeId: string) => void
  headingRef?: React.Ref<HTMLHeadingElement>
}) {
  const receipt = state?.receipt ?? ballot.receipt
  const titleId = `ballot-${ballot.id}-title`
  const busy = state?.submit.phase === 'submitting' || state?.submit.phase === 'checking'
  return (
    <section aria-labelledby={titleId} className="mt-8 first:mt-6">
      <div className={showTitle ? '' : 'sr-only'}>
        <h2 id={titleId} ref={headingRef} tabIndex={-1} className="type-display text-[1.45rem] outline-none">
          {ballot.title}
        </h2>
        {ballot.description ? <p className="mt-1 text-plum-soft">{ballot.description}</p> : null}
      </div>
      {receipt ? (
        <div className={showTitle ? 'mt-3' : ''}>
          {state?.note ? (
            <Notice tone="info" className="mb-3" role="status">
              {state.note}
            </Notice>
          ) : null}
          <ReceiptStub receipt={receipt} timezone={timezone} round={round} celebrate={state?.celebrate} />
        </div>
      ) : votable ? (
        <div role="radiogroup" aria-labelledby={titleId} aria-required className={`space-y-2.5 ${showTitle ? 'mt-3' : ''}`}>
          <p className="mb-1 text-sm text-plum-soft" id={`${titleId}-hint`}>
            {ballot.kind === 'team'
              ? 'Choose one team. Your vote goes to the whole team, not to one member. It is final once submitted.'
              : 'Choose one person. Your vote is final once submitted.'}
          </p>
          {ballot.nominees.map((nominee) => (
            <NomineeOption
              key={nominee.id}
              ballot={ballot}
              nominee={nominee}
              checked={state?.draft === nominee.id || (busy && 'nomineeId' in (state?.submit ?? {}) && (state?.submit as { nomineeId: string }).nomineeId === nominee.id)}
              disabled={busy}
              onSelect={() => onSelect(nominee.id)}
            />
          ))}
        </div>
      ) : (
        <div className={showTitle ? 'mt-3' : ''}>
          <p className="flex items-center gap-2 font-semibold text-plum-soft">
            <CircleSlash aria-hidden className="size-4" />
            Voting closed. No vote was recorded for you on this ballot.
          </p>
          <ul className="mt-3 space-y-2">
            {ballot.nominees.map((n) => (
              <li key={n.id} className="flex items-center gap-3 rounded-2xl border border-sand bg-paper/70 px-3 py-2.5">
                <Avatar src={n.photo_url} name={n.name} size={40} rounded={ballot.kind === 'team' ? 'xl' : 'full'} />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{n.name}</span>
                  {n.affiliation ? <span className="block truncate text-sm text-plum-soft">{n.affiliation}</span> : null}
                  {ballot.kind === 'team' && n.members?.length ? <TeamMembers members={n.members} className="text-sm" /> : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

export function EventDetailView({ eventId }: { eventId: string }) {
  const router = useRouter()
  const p = useParticipant()
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [loadError, setLoadError] = useState<ApiError | null>(null)
  // The round detail must be as current as the snapshot before voting is allowed.
  const [detailStale, setDetailStale] = useState(false)
  const [focusBallot, setFocusBallot] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')
  const [confettiKey, setConfettiKey] = useState<string | null>(null)
  const headingRefs = useRef<Record<string, HTMLHeadingElement | null>>({})
  const now = useServerNow(1000)
  const snapshot = p.snapshot

  const load = useCallback(async () => {
    const started = Date.now()
    try {
      const data = await participantApi<EventDetail>(`/me/events/${eventId}`, { timeoutMs: 10_000 })
      serverClock.observe(data.server_time, started, Date.now())
      setDetail(data)
      setLoadError(null)
      setDetailStale(false)
    } catch (error) {
      setDetailStale(true)
      if (error instanceof ApiError) setLoadError(error)
    }
  }, [eventId])

  // Refetch whenever the participant snapshot changes (state version, reconnects).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetching external state
    void load()
  }, [load, p.revision])

  useLayoutEffect(() => {
    if (detail) notifyPageReady()
  }, [detail])

  const onRecorded = useCallback(() => {
    void p.refresh()
  }, [p])
  const { state, select, submit } = useVoting(detail, snapshot?.participant.user_code ?? null, onRecorded)

  const event = detail?.event
  const live = event?.status === 'live' && !!event.voting_deadline_at && now < Date.parse(event.voting_deadline_at)
  const deadlinePassed = event?.status === 'live' && !!event.voting_deadline_at && now >= Date.parse(event.voting_deadline_at)

  // When the deadline passes on the client clock, confirm with the server shortly after.
  useEffect(() => {
    if (!deadlinePassed) return
    const id = window.setTimeout(() => void p.refresh(), 1500)
    return () => window.clearTimeout(id)
  }, [deadlinePassed, p])

  const ballots = useMemo(() => detail?.ballots ?? [], [detail])
  const unvoted = ballots.filter((b) => !(state[b.id]?.receipt ?? b.receipt))
  const target =
    unvoted.find((b) => b.id === focusBallot) ?? unvoted.find((b) => state[b.id]?.draft) ?? unvoted[0] ?? null
  const targetState = target ? state[target.id] : undefined
  const targetBusy = targetState?.submit.phase === 'submitting' || targetState?.submit.phase === 'checking'

  // Celebrate only after a committed receipt; announce it for screen readers.
  const lastCelebrated = useRef<string | null>(null)
  useEffect(() => {
    for (const ballot of ballots) {
      const s = state[ballot.id]
      if (s?.receipt && s.celebrate && lastCelebrated.current !== s.receipt.receipt_code) {
        lastCelebrated.current = s.receipt.receipt_code
        setConfettiKey(s.receipt.receipt_code)
        setAnnounce(`Vote recorded for ${s.receipt.nominee_name}. Receipt ${s.receipt.receipt_code}.`)
        headingRefs.current[ballot.id]?.focus({ preventScroll: true })
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        document
          .querySelector(`[data-receipt="${ballot.id}"]`)
          ?.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
      }
    }
  }, [state, ballots])

  const phaseAnnouncement =
    targetState?.submit.phase === 'submitting'
      ? 'Submitting your vote…'
      : targetState?.submit.phase === 'checking'
        ? 'Checking your vote. Please keep this page open.'
        : targetState?.submit.phase === 'error'
          ? targetState.submit.message
          : null

  if (loadError && !detail) {
    return (
      <main>
        <PageColumn className="pt-6">
          <BackLink />
          <Notice
            className="mt-6"
            tone={loadError.code === 'EVENT_NOT_RELEASED' ? 'info' : 'danger'}
            role="alert"
            title={loadError.code === 'EVENT_NOT_RELEASED' ? 'This round has not opened yet' : 'We could not load this round'}
            action={<Button variant="secondary" onClick={() => router.push('/lobby')}>Back to programme</Button>}
          >
            {loadError.code === 'EVENT_NOT_RELEASED' ? 'It unlocks when the host opens it.' : loadError.message}
          </Notice>
        </PageColumn>
      </main>
    )
  }
  if (!detail || !event || !snapshot) {
    return (
      <main aria-busy>
        <PageColumn className="pt-6">
          <BackLink />
          <div className="skeleton mt-6 size-16 rounded-[16px]" />
          <div className="skeleton mt-4 h-10 w-3/4" />
          <div className="skeleton mt-6 h-[4.25rem] w-full rounded-[1.25rem]" />
          <div className="skeleton mt-2.5 h-[4.25rem] w-full rounded-[1.25rem]" />
          <div className="skeleton mt-2.5 h-[4.25rem] w-full rounded-[1.25rem]" />
        </PageColumn>
      </main>
    )
  }

  const tz = detail.timezone
  const otherLive =
    snapshot.live_event_id && snapshot.live_event_id !== event.id
      ? snapshot.events.find((e) => e.id === snapshot.live_event_id) ?? null
      : null
  const allVoted = ballots.length > 0 && unvoted.length === 0
  const multi = ballots.length > 1
  const closedWhileOpen = !live && unvoted.length > 0 && (event.status === 'closed' || deadlinePassed)
  const detailCurrent = !detailStale && detail.state_version >= snapshot.state_version
  const canSubmit = live && p.authoritative && detailCurrent && !!targetState?.draft && !targetBusy
  const draftNominee = target?.nominees.find((n) => n.id === (targetState?.draft ?? (targetState?.submit as { nomineeId?: string })?.nomineeId))

  let submitLabel = 'Submit vote'
  if (!live) submitLabel = 'Voting closed'
  else if (!p.authoritative || !detailCurrent) submitLabel = 'Waiting for connection'
  else if (targetState?.submit.phase === 'submitting') submitLabel = 'Submitting…'
  else if (targetState?.submit.phase === 'checking') submitLabel = 'Checking your vote…'

  const showBar = live && !!target
  return (
    <main className={showBar ? 'pb-48' : 'pb-16'}>
      <Confetti fireKey={confettiKey} />
      <p className="sr-only" aria-live="polite" role="status">
        {phaseAnnouncement ?? announce}
      </p>
      <PageColumn className="pt-2">
        <BackLink />
        {otherLive ? (
          <Notice
            className="mt-3"
            tone="info"
            role="status"
            title={`Next event is live: ${otherLive.name ?? `Round ${otherLive.round}`}`}
            action={
              <Button size="sm" onClick={() => navigateWithTransition(() => router.push(`/event/${otherLive.id}`))}>
                Go to {otherLive.name ?? 'the live round'}
                <ArrowRight aria-hidden className="size-4" />
              </Button>
            }
          >
            {unvoted.length && !allVoted ? 'Any choice you have not submitted here was not sent.' : null}
          </Notice>
        ) : null}

        <header className="mt-4">
          <div className="flex items-center gap-4">
            <EventLogo src={event.logo_url} name={event.name} size={68} transitionId={transitionName('logo', event.id)} />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-plum-soft">
                Round {event.round} of {detail.event_count}
              </p>
              <h1
                className={`type-display text-plum [overflow-wrap:anywhere] ${titleSize(event.name, 'detail')}`}
                style={{ viewTransitionName: transitionName('title', event.id) }}
              >
                {event.name}
              </h1>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
            {allVoted ? (
              <StatusChip kind="recorded" />
            ) : live ? (
              <StatusChip kind="live" />
            ) : (
              <StatusChip kind="closed" />
            )}
            {live && event.voting_deadline_at ? (
              <span className="text-sm text-plum-soft">
                Closes {timeLabel(event.voting_deadline_at, tz)},{' '}
                <Countdown deadline={event.voting_deadline_at} className="font-semibold text-plum" /> left
              </span>
            ) : event.closed_at ? (
              <span className="text-sm text-plum-soft">Closed {timeLabel(event.closed_at, tz)}</span>
            ) : null}
          </div>
          {event.description ? <p className="mt-4 leading-relaxed text-plum-soft">{event.description}</p> : null}
          {event.rules ? (
            <details className="group mt-3 rounded-2xl border border-sand bg-paper/70 px-4 [&_summary::-webkit-details-marker]:hidden">
              <summary className="flex min-h-11 cursor-pointer items-center justify-between font-semibold">
                How voting works
                <span aria-hidden className="text-plum-soft transition-transform duration-150 group-open:rotate-45">+</span>
              </summary>
              <p className="pb-3 text-plum-soft">{event.rules}</p>
            </details>
          ) : null}
        </header>

        {closedWhileOpen ? (
          <Notice className="mt-5" tone="warning" role="alert" title="Voting for this round has closed">
            Choices that were not submitted were not recorded.
          </Notice>
        ) : null}

        {ballots.map((ballot) => (
          <BallotSection
            key={ballot.id}
            ballot={ballot}
            state={state[ballot.id]}
            votable={live}
            showTitle={multi}
            timezone={tz}
            round={event.round}
            onSelect={(nomineeId) => {
              setFocusBallot(ballot.id)
              select(ballot.id, nomineeId)
            }}
            headingRef={(el) => {
              headingRefs.current[ballot.id] = el
            }}
          />
        ))}

        {detail.results?.length ? (
          <section aria-label="Results" className="mt-8 space-y-3">
            <h2 className="type-display flex items-center gap-2 text-[1.45rem]">
              <Sparkles aria-hidden className="size-5 text-gold-deep" />
              Results
            </h2>
            {detail.results.map((r) => (
              <ResultsView key={r.ballot_id} result={r} />
            ))}
          </section>
        ) : null}

        {allVoted ? (
          <div className="enter-up mt-6 rounded-[var(--radius-card)] border border-sand bg-paper p-5">
            <p className="font-semibold">What happens next</p>
            <p className="mt-1 text-plum-soft">
              {otherLive
                ? 'The next round is already live.'
                : 'When the host opens the next round, it appears at the top of the programme.'}
            </p>
            <Button
              className="mt-4"
              variant="secondary"
              block
              onClick={() => navigateWithTransition(() => router.push(otherLive ? `/event/${otherLive.id}` : '/lobby'))}
            >
              {otherLive ? `Go to ${otherLive.name ?? 'the live round'}` : 'Back to programme'}
            </Button>
          </div>
        ) : null}
      </PageColumn>

      {showBar && target ? (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-sand bg-paper/95 backdrop-blur-md safe-bottom">
          <PageColumn className="pt-3">
            <div aria-live="polite">
              {targetState?.submit.phase === 'error' ? (
                <p role="alert" className="mb-2 text-sm font-medium text-danger">
                  {targetState.submit.message}
                </p>
              ) : targetState?.submit.phase === 'checking' ? (
                <p className="mb-2 flex items-start gap-2 text-sm text-plum-soft">
                  <LoaderCircle aria-hidden className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
                  The connection dropped while sending. We are confirming whether your vote was recorded. Keep this page open.
                </p>
              ) : null}
            </div>
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs text-plum-soft">{multi ? target.title : 'Your choice'}</p>
                <p className={`truncate font-semibold ${draftNominee ? '' : 'text-plum-soft'}`}>
                  {draftNominee?.name ?? `Choose one ${target.kind === 'team' ? 'team' : 'person'}`}
                </p>
              </div>
              <Button
                size="lg"
                className="shrink-0"
                disabled={!canSubmit}
                busy={targetBusy}
                onClick={() => void submit(target.id)}
              >
                {submitLabel}
              </Button>
            </div>
          </PageColumn>
        </div>
      ) : null}
    </main>
  )
}

function BackLink() {
  const router = useRouter()
  return (
    <button
      type="button"
      onClick={() =>
        navigateWithTransition(() => {
          if (window.history.length > 1 && document.referrer.startsWith(window.location.origin)) router.back()
          else router.push('/lobby')
        })
      }
      className="-ml-2 inline-flex min-h-11 items-center gap-2 rounded-full px-2 font-semibold text-primary hover:bg-primary-wash"
    >
      <ArrowLeft aria-hidden className="size-4" />
      Programme
    </button>
  )
}
