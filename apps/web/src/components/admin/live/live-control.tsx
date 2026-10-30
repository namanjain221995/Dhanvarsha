'use client'

import {
  CalendarClock,
  CircleDashed,
  Clock3,
  Hourglass,
  Lock,
  Megaphone,
  Pencil,
  Play,
  Power,
  RefreshCw,
  RotateCcw,
  SkipForward,
  Square,
  TimerReset,
} from 'lucide-react'
import Link from 'next/link'
import { useCallback, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { adminApi } from '@/lib/api'
import { describeError, errorCode, goToLogin, isApiError, isUnauthenticated } from '@/lib/admin/errors'
import { ballotKindLabel, ballotTurnout, CLOSE_REASONS, isQuestion, SCHEDULE_JOB_LABELS, plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { LiveEvent, LiveState, ScheduleJob, SchedulePreview } from '@/lib/admin/types'
import { formatClock, formatRelative, formatWhen, zoneLabel } from '@/lib/admin/zoned'
import type { LiveStatus } from '@/lib/live'
import { localEquivalent, useServerNow, zoneAbbreviation } from '@/lib/time'
import { OrganiserNote } from '../organiser-note'
import {
  Bar,
  FlashRegion,
  LoadError,
  MiniButton,
  PageHeader,
  PageSkeleton,
  Panel,
  StatusChip,
  useDialog,
  useFlash,
  type Tone,
} from '../primitives'
import { TimeLeft } from './deadline'
import {
  accessPhase,
  ChangeDeadlineDialog,
  CloseVotingDialog,
  EndProgrammeDialog,
  OpenEventDialog,
  ResumeProgrammeDialog,
  ResumeScheduleDialog,
  RestartProgrammeDialog,
  StartNextDialog,
  type LiveBody,
} from './dialogs'

type LiveDialog =
  | { kind: 'open'; eventId?: string }
  | { kind: 'start-next'; eventId?: string }
  | { kind: 'close' }
  | { kind: 'deadline' }
  | { kind: 'end' }
  | { kind: 'reopen' }
  | { kind: 'restart' }
  | { kind: 'resume-schedule'; preview: SchedulePreview }

const STALE_MESSAGE = 'Another organiser or the schedule changed the live state. Review and try again.'

export function LiveControl() {
  const { me, connection } = useAdminSession()
  const live = useAdminQuery<LiveState>('/admin/live')
  const flash = useFlash()
  const dialog = useDialog<LiveDialog>()
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<React.ReactNode>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  // The live version the organiser was looking at when the dialog opened. Actions send it,
  // so a change made meanwhile (another organiser, the schedule) is refused, not overridden.
  const [openedVersion, setOpenedVersion] = useState<number | null>(null)

  // Votes arrive as "tally" signals: refetch at most once every 1.5 s, never per vote.
  useAdminSignal((signal) => {
    if (signal.type === 'tally') live.reloadSoon(1500)
    else if (signal.type !== 'email' && signal.type !== 'revoked') void live.reload()
  })

  const state = live.data
  const tz = state?.programme.timezone ?? me.programme.timezone
  const reload = live.reload
  const onExpire = useCallback(() => void reload(), [reload])

  const openDialog = (next: LiveDialog, from?: Element | null) => {
    setActionError(null)
    setOpenedVersion(live.data?.live_version ?? null)
    dialog.open(next, from)
  }

  const nameOf = (id: unknown) => state?.events.find((event) => event.id === id)?.name ?? 'the event'

  const run = async <T,>(path: string, body: LiveBody, success: string | ((result: T) => string)) => {
    if (!state) return
    setBusy(true)
    setActionError(null)
    try {
      const result = await adminAction<T>(path, {
        body: { expected_live_version: openedVersion ?? state.live_version, ...body },
      })
      dialog.close()
      flash.show({ tone: 'success', message: typeof success === 'string' ? success : success(result) })
      void live.reload()
    } catch (error) {
      const code = errorCode(error)
      if (code === 'STALE_STATE') {
        dialog.close()
        flash.show({ tone: 'warning', title: 'The live state changed', message: STALE_MESSAGE })
        void live.reload()
      } else if (isApiError(error) && error.isNetwork) {
        dialog.close()
        flash.show({
          tone: 'warning',
          title: 'No answer from the server',
          message: 'The action may or may not have gone through. The page is refreshing; check the live state before trying again.',
        })
        void live.reload()
      } else if (code === 'EVENT_NOT_READY' && typeof body.event_id === 'string') {
        setActionError(
          <>
            {describeError(error)}{' '}
            <Link href={`/admin/events/${body.event_id}`} className="font-semibold text-primary underline underline-offset-2">
              Edit {nameOf(body.event_id)}
            </Link>
          </>,
        )
      } else {
        setActionError(describeError(error))
      }
    } finally {
      setBusy(false)
    }
  }

  const startResume = async (from: Element | null) => {
    setPreviewBusy(true)
    try {
      const preview = await adminApi<SchedulePreview>('/admin/schedule/preview')
      openDialog({ kind: 'resume-schedule', preview }, from)
    } catch (error) {
      if (isUnauthenticated(error)) goToLogin()
      else flash.fail(error, 'Could not load the schedule plan')
    } finally {
      setPreviewBusy(false)
    }
  }

  const refreshNow = () => {
    setRefreshing(true)
    void live.reload().finally(() => setRefreshing(false))
  }

  if (!state) {
    return (
      <>
        <PageHeader title="Live control" />
        <div className="mt-6">{live.error ? <LoadError error={live.error} onRetry={refreshNow} what="the live state" /> : <PageSkeleton rows={2} />}</div>
      </>
    )
  }

  const current = state.current_event
  const p = state.programme
  const scheduled = p.run_mode === 'scheduled'

  const changedMeanwhile = dialog.value !== null && openedVersion !== null && openedVersion !== state.live_version
  const dialogProps = {
    state,
    timeZone: tz,
    busy,
    error:
      actionError ??
      (changedMeanwhile
        ? 'The live state changed while this was open, so confirming will be refused. Close this, review the latest state and try again.'
        : null),
    onClose: dialog.close,
  }

  return (
    <>
      <PageHeader
        title="Live control"
        description={`What participants can vote on right now. Times are in ${zoneLabel(tz, state.server_time)}.`}
        actions={
          <>
            <p className="text-sm text-plum-soft">
              Last refreshed <span className="tabular font-semibold text-plum">{formatClock(state.server_time, tz)}</span>
            </p>
            <MiniButton busy={refreshing} onClick={refreshNow}>
              {refreshing ? null : <RefreshCw aria-hidden className="size-4" />}
              Refresh
            </MiniButton>
          </>
        }
      />

      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />

      <div className="mt-5 space-y-3 empty:hidden">
        {live.error ? (
          <Notice
            tone="warning"
            role="alert"
            title="Could not refresh"
            action={<MiniButton onClick={refreshNow}>Try again</MiniButton>}
          >
            {describeError(live.error)} Showing the state from {formatClock(state.server_time, tz)}.
          </Notice>
        ) : null}
        {p.ended_at ? (
          <Notice
            tone="warning"
            title="The programme has ended"
            action={
              <Button variant="secondary" onClick={(event) => openDialog({ kind: 'reopen' }, event.currentTarget)}>
                Resume programme…
              </Button>
            }
          >
            Ended {formatWhen(p.ended_at, tz, Date.parse(state.server_time))}. No event can open until you resume the
            programme.
          </Notice>
        ) : null}
        {scheduled && p.schedule_suspended ? (
          <Notice
            tone="warning"
            title="Manual override active"
            action={
              <Button
                variant="secondary"
                busy={previewBusy}
                onClick={(event) => void startResume(event.currentTarget)}
              >
                Resume schedule…
              </Button>
            }
          >
            The live state was changed by hand
            {p.schedule_suspended_at ? ` at ${formatWhen(p.schedule_suspended_at, tz, Date.parse(state.server_time))}` : ''}, so the
            schedule is paused. Scheduled events will not open on their own until you resume it.
          </Notice>
        ) : null}
        <AccessBanner state={state} timeZone={tz} />
        {!state.worker.healthy ? (
          <Notice tone="danger" title="The background worker is not running">
            Votes still stop at each deadline, but events are not marked closed, scheduled events do not open and emails
            are not sent until it runs again.
          </Notice>
        ) : null}
      </div>

      <div className="mt-6 flex flex-col gap-5 xl:grid xl:grid-cols-[minmax(0,1fr)_22.5rem] xl:items-start">
        <div className="contents xl:flex xl:min-w-0 xl:flex-col xl:gap-5">
          <div className="order-1 min-w-0">
            {current ? (
              <OnAirCard
                state={state}
                timeZone={tz}
                onExpire={onExpire}
                onClose={(from) => openDialog({ kind: 'close' }, from)}
                onDeadline={(from) => openDialog({ kind: 'deadline' }, from)}
                onStartNext={(from) => openDialog({ kind: 'start-next' }, from)}
              />
            ) : (
              <IdleCard state={state} onOpen={(eventId, from) => openDialog({ kind: 'open', eventId }, from)} />
            )}
          </div>
          <div className="order-5 min-w-0">
            <RoundsPanel
              state={state}
              timeZone={tz}
              onOpen={(eventId, from) => openDialog(current ? { kind: 'start-next', eventId } : { kind: 'open', eventId }, from)}
            />
          </div>
        </div>
        <div className="contents xl:flex xl:min-w-0 xl:flex-col xl:gap-5">
          <div className="order-2 min-w-0">
            <UpNextPanel state={state} timeZone={tz} />
          </div>
          <div className="order-3 min-w-0">
            <SchedulePanel
              state={state}
              timeZone={tz}
              previewBusy={previewBusy}
              onResume={(from) => void startResume(from)}
            />
          </div>
          <div className="order-4 min-w-0 space-y-5">
            <HealthPanel state={state} connection={connection} />
            {!p.ended_at ? (
              <Panel title="End of the show" description="Stops all voting until you resume the programme.">
                <Button variant="secondary" onClick={(event) => openDialog({ kind: 'end' }, event.currentTarget)}>
                  <Power aria-hidden className="size-4" />
                  End programme…
                </Button>
              </Panel>
            ) : null}
            {p.ended_at || state.events.some((e) => e.status !== 'upcoming') ? (
              <Panel
                title="Start again"
                description="Puts every round back to the start, for example after a rehearsal. Votes so far stop counting."
              >
                <Button variant="secondary" onClick={(event) => openDialog({ kind: 'restart' }, event.currentTarget)}>
                  <RotateCcw aria-hidden className="size-4" />
                  Restart from round 1…
                </Button>
              </Panel>
            ) : null}
          </div>
        </div>
      </div>

      {dialog.value?.kind === 'open' ? (
        <OpenEventDialog
          key={dialog.key}
          {...dialogProps}
          initialEventId={dialog.value.eventId}
          onSubmit={(body) => void run('/admin/live/open', body, `Voting is open for ${nameOf(body.event_id)}.`)}
        />
      ) : null}
      {dialog.value?.kind === 'start-next' ? (
        <StartNextDialog
          key={dialog.key}
          {...dialogProps}
          initialEventId={dialog.value.eventId}
          onSubmit={(body) =>
            void run(
              '/admin/live/start-next',
              body,
              current
                ? `${current.name} is closed. Voting is open for ${nameOf(body.event_id)}.`
                : `Voting is open for ${nameOf(body.event_id)}.`,
            )
          }
        />
      ) : null}
      {dialog.value?.kind === 'close' ? (
        <CloseVotingDialog
          key={dialog.key}
          {...dialogProps}
          onSubmit={(body) => void run('/admin/live/close', body, `Voting closed for ${current?.name ?? 'the event'}.`)}
        />
      ) : null}
      {dialog.value?.kind === 'deadline' ? (
        <ChangeDeadlineDialog
          key={dialog.key}
          {...dialogProps}
          onSubmit={(body) =>
            void run(
              '/admin/live/deadline',
              body,
              `New deadline for ${current?.name ?? 'the event'}: ${formatWhen(String(body.deadline_at), tz, Date.parse(state.server_time))}.`,
            )
          }
        />
      ) : null}
      {dialog.value?.kind === 'end' ? (
        <EndProgrammeDialog
          key={dialog.key}
          {...dialogProps}
          onSubmit={(body) => void run('/admin/live/end-programme', body, 'The programme has ended. Voting is closed.')}
        />
      ) : null}
      {dialog.value?.kind === 'reopen' ? (
        <ResumeProgrammeDialog
          key={dialog.key}
          {...dialogProps}
          onSubmit={(body) => void run('/admin/live/reopen-programme', body, 'The programme is running again. You can open events.')}
        />
      ) : null}
      {dialog.value?.kind === 'restart' ? (
        <RestartProgrammeDialog
          key={dialog.key}
          {...dialogProps}
          onSubmit={(body) =>
            void run<{ events: number }>(
              '/admin/live/restart-programme',
              body,
              (result) => `Restarted: all ${result.events} rounds are back at the start. Open round 1 when you are ready.`,
            )
          }
        />
      ) : null}
      {dialog.value?.kind === 'resume-schedule' ? (
        <ResumeScheduleDialog
          key={dialog.key}
          preview={dialog.value.preview}
          timeZone={tz}
          busy={busy}
          error={actionError}
          onClose={dialog.close}
          onSubmit={(body) =>
            void run<{ ok: boolean; jobs: number }>('/admin/schedule/resume', body, (result) =>
              result.jobs
                ? `The schedule is running again. ${plural(result.jobs, 'scheduled opening')} queued.`
                : 'The schedule is running again. No event has a future planned time yet.',
            )
          }
        />
      ) : null}
    </>
  )
}

// ---- banners ------------------------------------------------------------------

function AccessBanner({ state, timeZone }: { state: LiveState; timeZone: string }) {
  const now = useServerNow(5000)
  const p = state.programme
  if (p.ended_at) return null
  const phase = accessPhase(p, now)
  if (phase === 'open') return null
  const link = (
    <Link href="/admin/programme" className="font-semibold text-primary underline underline-offset-2">
      Programme settings
    </Link>
  )
  if (phase === 'before_open') {
    return (
      <Notice tone="info" title="Programme access is not open yet">
        {p.login_opens_at
          ? `Participants can sign in and vote from ${formatWhen(p.login_opens_at, timeZone, now)}. Events can only open after that.`
          : 'No opening time is set, so participants cannot sign in and no event can open.'}{' '}
        Change the access window in {link}.
      </Notice>
    )
  }
  return (
    <Notice tone="warning" title="Programme access has closed">
      Access closed {formatWhen(p.login_closes_at, timeZone, now)}. No event can open until you extend the access window in{' '}
      {link}.
    </Notice>
  )
}

// ---- on air -------------------------------------------------------------------

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0 rounded-2xl bg-ivory px-3 py-2.5 sm:px-4 sm:py-3">
      <dt className="text-[0.8rem] leading-snug text-plum-soft sm:text-sm">{label}</dt>
      <dd className="tabular mt-0.5 text-[1.35rem] font-bold leading-tight text-plum sm:text-[1.6rem]">
        {value.toLocaleString('en-IN')}
      </dd>
    </div>
  )
}

function OnAirCard({
  state,
  timeZone,
  onExpire,
  onClose,
  onDeadline,
  onStartNext,
}: {
  state: LiveState
  timeZone: string
  onExpire: () => void
  onClose: (from: Element) => void
  onDeadline: (from: Element) => void
  onStartNext: (from: Element) => void
}) {
  const event = state.current_event
  if (!event) return null
  const closing = event.effective_status === 'closing'
  const deadline = event.voting_deadline_at
  const serverNow = Date.parse(state.server_time)
  const local = localEquivalent(deadline, timeZone)
  const ballots = event.results ?? []

  return (
    <section
      aria-labelledby="on-air-title"
      className="overflow-hidden rounded-[var(--radius-hero)] border border-sand bg-paper shadow-[var(--shadow-card)]"
    >
      <div aria-hidden className="h-1 bg-gradient-to-r from-gold via-gold/60 to-gold/15" />
      <div className="p-5 sm:p-7">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {closing ? (
            <StatusChip tone="warning" icon={<Hourglass aria-hidden className="size-3.5" />} label="Closing" />
          ) : (
            <StatusChip tone="live" label="Live now" />
          )}
          <span className="text-sm font-semibold text-plum-soft">
            Round {event.round} of {state.events.length}
          </span>
        </div>
        <h2 id="on-air-title" className="type-display mt-3 break-words text-[2.35rem] text-plum sm:text-[3.1rem]">
          {event.name}
        </h2>
        <p className="mt-1.5 text-sm text-plum-soft">
          Opened {formatWhen(event.opened_at, timeZone, serverNow)}{' '}
          {event.opened_by === 'schedule' ? 'by the schedule' : 'by an organiser'}.
        </p>

        {closing ? (
          <Notice tone="warning" className="mt-5">
            The deadline passed at {formatWhen(deadline, timeZone, serverNow)}, so votes are no longer accepted. The event
            shows as closed once the background worker catches up, or when you close it now.
          </Notice>
        ) : null}

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <div className="min-w-0">
            <p className="text-sm text-plum-soft">Voting closes</p>
            <p className="mt-0.5 text-[1.35rem] font-bold leading-tight text-plum">
              {deadline ? formatWhen(deadline, timeZone, serverNow) : 'No deadline'}
              {deadline ? (
                <span className="ml-1.5 text-sm font-semibold text-plum-soft">{zoneAbbreviation(timeZone, deadline)}</span>
              ) : null}
            </p>
            {local ? <p className="mt-0.5 text-sm text-plum-soft">{local}</p> : null}
          </div>
          {deadline && !closing ? (
            <div className="min-w-0">
              <p className="text-sm text-plum-soft">Time left</p>
              <p className="type-display mt-0.5 text-[2.6rem] leading-none text-plum">
                <TimeLeft deadline={deadline} onExpire={onExpire} />
              </p>
            </div>
          ) : null}
        </div>

        <div className="mt-7">
          <div className="flex flex-wrap items-center justify-between gap-x-4">
            <h3 className="text-sm font-semibold text-plum">Turnout by ballot</h3>
            <Link
              href={`/admin/results?event=${event.id}`}
              className="inline-flex min-h-11 items-center text-sm font-semibold text-primary hover:underline"
            >
              Provisional results
            </Link>
          </div>
          {ballots.length === 0 ? (
            <p className="mt-2 text-sm text-plum-soft">No voting in this round: it has no ballots or questions to count.</p>
          ) : (
            <ul className="mt-1 space-y-5">
              {ballots.map((ballot) => {
                const { total, eligible, remaining } = ballotTurnout(ballot)
                const pct = eligible ? Math.round((total / eligible) * 100) : 0
                const answered = isQuestion(ballot.kind)
                return (
                  <li key={ballot.ballot_id}>
                    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
                      <p className="min-w-0 break-words font-semibold text-plum">
                        {ballot.title}
                        <span className="ml-2 text-sm font-normal text-plum-soft">{ballotKindLabel(ballot.kind)}</span>
                      </p>
                      <p className="tabular text-sm text-plum">
                        <strong className="text-base">{total.toLocaleString('en-IN')}</strong> of {eligible.toLocaleString('en-IN')}{' '}
                        {answered ? 'answered' : 'voted'} ({pct}%)
                      </p>
                    </div>
                    <div className="mt-2">
                      <Bar value={total} max={eligible} size="lg" />
                    </div>
                    <p className="tabular mt-1.5 text-sm text-plum-soft">
                      {remaining.toLocaleString('en-IN')} still to {answered ? 'answer' : 'vote'}
                    </p>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <dl className="mt-6 grid grid-cols-3 gap-2 sm:gap-3">
          <Stat label="Voted this round" value={event.voters} />
          <Stat label="Signed in now" value={state.turnout.signed_in} />
          <Stat label="Eligible voters" value={state.turnout.eligible} />
        </dl>

        <div className="mt-6 flex flex-col gap-2 border-t border-sand pt-5 sm:flex-row sm:flex-wrap">
          {state.next_event ? (
            <Button onClick={(e) => onStartNext(e.currentTarget)}>
              <SkipForward aria-hidden className="size-4" />
              Start next event…
            </Button>
          ) : null}
          <Button variant="secondary" onClick={(e) => onClose(e.currentTarget)}>
            <Square aria-hidden className="size-4" />
            Close voting…
          </Button>
          {!closing ? (
            <Button variant="secondary" onClick={(e) => onDeadline(e.currentTarget)}>
              <TimerReset aria-hidden className="size-4" />
              Change deadline…
            </Button>
          ) : null}
        </div>
        {/* Below the buttons, so a long note never pushes them off a laptop screen. */}
        <OrganiserNote note={event.organiser_note} eventName={event.name} className="mt-4" />
      </div>
    </section>
  )
}

function IdleCard({ state, onOpen }: { state: LiveState; onOpen: (eventId: string | undefined, from: Element) => void }) {
  const next = state.next_event
  const ended = Boolean(state.programme.ended_at)
  const anyUpcoming = state.events.some((event) => event.status === 'upcoming')
  const title = ended ? 'The programme has ended' : !anyUpcoming && state.events.length ? 'Every event has closed' : 'No event is open for voting'
  const body = ended
    ? 'Voting is closed for everyone. Resume the programme to open more events.'
    : !state.events.length
      ? 'Create the first event on the Events page.'
      : !anyUpcoming
        ? 'All rounds have finished. Publish results, then end the programme.'
        : 'Participants see what is coming up next. Open voting when the round starts on stage.'
  return (
    <section aria-labelledby="idle-title" className="rounded-[var(--radius-hero)] border border-sand bg-paper p-5 sm:p-7">
      <StatusChip tone="neutral" icon={<CircleDashed aria-hidden className="size-3.5" />} label="Nothing live" />
      <h2 id="idle-title" className="type-display mt-3 text-[2.1rem] text-plum sm:text-[2.6rem]">
        {title}
      </h2>
      <p className="mt-2 max-w-xl text-plum-soft">{body}</p>
      {!ended && next ? (
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button onClick={(e) => onOpen(next.id, e.currentTarget)}>
            <Play aria-hidden className="size-4" />
            Open voting for {next.name}…
          </Button>
          <Button variant="secondary" onClick={(e) => onOpen(undefined, e.currentTarget)}>
            Choose another event…
          </Button>
        </div>
      ) : null}
      {!state.events.length ? (
        <Link
          href="/admin/events"
          className="mt-6 inline-flex min-h-11 items-center gap-2 rounded-full bg-primary px-5 font-semibold text-white hover:bg-primary-deep"
        >
          Go to events
        </Link>
      ) : null}
      <dl className="mt-6 grid grid-cols-2 gap-2 sm:max-w-md sm:gap-3">
        <Stat label="Signed in now" value={state.turnout.signed_in} />
        <Stat label="Eligible voters" value={state.turnout.eligible} />
      </dl>
    </section>
  )
}

// ---- side panels --------------------------------------------------------------

function UpNextPanel({ state, timeZone }: { state: LiveState; timeZone: string }) {
  const next = state.next_event
  const serverNow = Date.parse(state.server_time)
  const p = state.programme
  return (
    <Panel title="Up next">
      {next ? (
        <div>
          <p className="text-sm font-semibold text-plum-soft">
            Round {next.round} of {state.events.length}
          </p>
          <p className="type-display mt-1 break-words text-[1.7rem] text-plum">{next.name}</p>
          <p className="mt-2 text-sm text-plum-soft">
            {next.planned_start_at
              ? `Planned ${formatWhen(next.planned_start_at, timeZone, serverNow)} to ${formatWhen(next.planned_end_at, timeZone, serverNow)}.`
              : 'No planned time.'}{' '}
            {p.run_mode === 'scheduled' && next.planned_start_at && !p.schedule_suspended && !p.ended_at
              ? 'The schedule opens it automatically.'
              : 'It opens when an organiser opens it.'}
          </p>
          <OrganiserNote note={next.organiser_note} eventName={next.name} className="mt-3" />
          <Link
            href={`/admin/events/${next.id}`}
            className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-full text-[0.95rem] font-semibold text-primary hover:underline"
          >
            <Pencil aria-hidden className="size-4" />
            Edit {next.name}
          </Link>
        </div>
      ) : (
        <p className="text-sm text-plum-soft">
          No upcoming events.{' '}
          <Link href="/admin/events" className="font-semibold text-primary underline underline-offset-2">
            Add an event
          </Link>
        </p>
      )}
    </Panel>
  )
}

const JOB_TONE: Record<ScheduleJob['status'], Tone> = {
  pending: 'neutral',
  done: 'success',
  skipped: 'neutral',
  missed: 'warning',
  stale: 'neutral',
  failed: 'danger',
}

function SchedulePanel({
  state,
  timeZone,
  previewBusy,
  onResume,
}: {
  state: LiveState
  timeZone: string
  previewBusy: boolean
  onResume: (from: Element) => void
}) {
  const [showAll, setShowAll] = useState(false)
  const p = state.programme
  const scheduled = p.run_mode === 'scheduled'
  const serverNow = Date.parse(state.server_time)
  const jobs = state.schedule_jobs
  const visible = showAll ? jobs : jobs.slice(0, 5)
  return (
    <Panel
      title="Schedule"
      actions={
        <Link href="/admin/programme" className="inline-flex min-h-11 items-center text-sm font-semibold text-primary hover:underline">
          Change run mode
        </Link>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        {scheduled ? (
          p.schedule_suspended ? (
            <StatusChip tone="warning" label="Scheduled, paused by manual override" />
          ) : (
            <StatusChip tone="success" icon={<CalendarClock aria-hidden className="size-3.5" />} label="Scheduled" />
          )
        ) : (
          <StatusChip tone="info" icon={<Pencil aria-hidden className="size-3.5" />} label="Manual" />
        )}
      </div>
      <p className="mt-2 text-sm text-plum-soft">
        {scheduled
          ? p.schedule_suspended
            ? 'Events open only when an organiser opens them, until you resume the schedule.'
            : 'Events open automatically at their planned start times.'
          : 'Events open only when an organiser opens them.'}
      </p>
      {scheduled && p.schedule_suspended ? (
        <MiniButton className="mt-3" busy={previewBusy} onClick={(e) => onResume(e.currentTarget)}>
          Resume schedule…
        </MiniButton>
      ) : null}

      <h3 className="mt-5 text-sm font-semibold text-plum">Recent schedule activity</h3>
      {jobs.length === 0 ? (
        <p className="mt-1 text-sm text-plum-soft">
          {scheduled ? 'Nothing scheduled yet. Give upcoming events planned times.' : 'None. The schedule is not in use.'}
        </p>
      ) : (
        <>
          <ul className="mt-2 divide-y divide-sand">
            {visible.map((job) => (
              <li key={job.id} className="py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 break-words text-[0.92rem] font-semibold text-plum">{job.event_name}</p>
                  <StatusChip tone={JOB_TONE[job.status]} label={SCHEDULE_JOB_LABELS[job.status]} />
                </div>
                <p className="mt-0.5 text-sm text-plum-soft">
                  {job.executed_at
                    ? `Ran ${formatWhen(job.executed_at, timeZone, serverNow)}. `
                    : `Due ${formatWhen(job.run_at, timeZone, serverNow)}. `}
                  Window {formatWhen(job.run_at, timeZone, serverNow)} to {formatWhen(job.window_end_at, timeZone, serverNow)}.
                </p>
                {job.detail && job.status !== 'done' ? (
                  <p className={`mt-0.5 text-sm ${job.status === 'failed' || job.status === 'missed' ? 'font-medium text-plum' : 'text-plum-soft'}`}>
                    {job.detail}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
          {jobs.length > 5 ? (
            <MiniButton variant="quiet" className="mt-1 -ml-3" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show fewer' : `Show all ${jobs.length}`}
            </MiniButton>
          ) : null}
        </>
      )}
    </Panel>
  )
}

function HealthRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <li className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[0.92rem] font-semibold text-plum">{label}</p>
        <p className="text-sm text-plum-soft">{detail}</p>
      </div>
      <StatusChip tone={ok ? 'success' : 'danger'} label={ok ? 'OK' : 'Problem'} />
    </li>
  )
}

function HealthPanel({ state, connection }: { state: LiveState; connection: LiveStatus }) {
  const serverNow = Date.parse(state.server_time)
  const worker = state.worker
  return (
    <Panel title="System health">
      <ul className="space-y-4">
        <HealthRow
          ok={worker.healthy}
          label="Background worker"
          detail={
            worker.healthy
              ? `Running, last seen ${formatRelative(worker.last_seen_at, serverNow)}. Sends email, closes events at their deadline and runs the schedule.`
              : worker.last_seen_at
                ? `Not responding. Last seen ${formatRelative(worker.last_seen_at, serverNow)}.`
                : 'Not running. Emails, automatic closing and the schedule are waiting.'
          }
        />
        <HealthRow
          ok={state.live_channel.listener_connected}
          label="Server update listener"
          detail={
            state.live_channel.listener_connected
              ? `Connected. ${plural(state.live_channel.connections_here, 'browser')} connected to this server.`
              : 'Disconnected. Pages still refresh, but changes can take a few seconds longer to show.'
          }
        />
        <HealthRow
          ok={connection === 'live'}
          label="This browser"
          detail={
            connection === 'live'
              ? 'Receiving live updates.'
              : connection === 'offline'
                ? 'Offline. Reconnect to see changes.'
                : 'Reconnecting. The page refreshes every 15 seconds meanwhile.'
          }
        />
      </ul>
    </Panel>
  )
}

// ---- rounds -------------------------------------------------------------------

function eventChip(event: LiveEvent) {
  switch (event.effective_status) {
    case 'open':
      return <StatusChip tone="live" label="Live now" />
    case 'closing':
      return <StatusChip tone="warning" icon={<Hourglass aria-hidden className="size-3.5" />} label="Closing" />
    case 'closed':
      return <StatusChip tone="neutral" icon={<Lock aria-hidden className="size-3.5" />} label="Closed" />
    default:
      return <StatusChip tone="neutral" icon={<Clock3 aria-hidden className="size-3.5" />} label="Upcoming" />
  }
}

function roundDetail(event: LiveEvent, timeZone: string, serverNow: number): string {
  if (event.status === 'upcoming') {
    return event.planned_start_at
      ? `Planned ${formatWhen(event.planned_start_at, timeZone, serverNow)} to ${formatWhen(event.planned_end_at, timeZone, serverNow)}.`
      : 'No planned time.'
  }
  const voters = plural(event.voters, 'voter')
  if (event.status === 'open') {
    return `Opened ${formatWhen(event.opened_at, timeZone, serverNow)}. Closes ${formatWhen(event.voting_deadline_at, timeZone, serverNow)}. ${voters}.`
  }
  const reason = event.close_reason ? (CLOSE_REASONS[event.close_reason] ?? 'Closed') : 'Closed'
  return `${reason}, ${formatWhen(event.closed_at, timeZone, serverNow)}. ${voters}.`
}

function RoundsPanel({
  state,
  timeZone,
  onOpen,
}: {
  state: LiveState
  timeZone: string
  onOpen: (eventId: string, from: Element) => void
}) {
  const serverNow = Date.parse(state.server_time)
  const live = Boolean(state.current_event)
  return (
    <Panel
      title={`All rounds (${state.events.length})`}
      actions={
        <Link href="/admin/events" className="inline-flex min-h-11 items-center text-sm font-semibold text-primary hover:underline">
          Manage events
        </Link>
      }
    >
      {state.events.length === 0 ? (
        <p className="text-sm text-plum-soft">No events yet.</p>
      ) : (
        <ol className="-mx-1 divide-y divide-sand">
          {state.events.map((event) => {
            const isLive = event.status === 'open'
            return (
              <li key={event.id} className={`flex items-start gap-3 px-1 py-3 ${isLive ? 'rounded-xl bg-gold-wash/60' : ''}`}>
                <span
                  aria-hidden
                  className={`tabular mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl text-sm font-bold ${
                    isLive ? 'bg-paper text-gold-deep ring-1 ring-gold/50' : 'bg-plum/[0.06] text-plum-soft'
                  }`}
                >
                  {event.round}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <p className="min-w-0 break-words font-semibold text-plum">
                      <span className="sr-only">Round {event.round}: </span>
                      {event.name}
                    </p>
                    {eventChip(event)}
                    {event.results_published ? (
                      <StatusChip tone="success" icon={<Megaphone aria-hidden className="size-3.5" />} label="Results published" />
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-sm text-plum-soft">{roundDetail(event, timeZone, serverNow)}</p>
                  {event.status === 'upcoming' ? (
                    <OrganiserNote note={event.organiser_note} eventName={event.name} className="mt-2" />
                  ) : null}
                </div>
                <div className="shrink-0">
                  {event.status === 'upcoming' && !state.programme.ended_at ? (
                    <MiniButton
                      variant="link"
                      onClick={(e) => onOpen(event.id, e.currentTarget)}
                      aria-label={`${live ? 'Start' : 'Open'} ${event.name}…`}
                    >
                      {live ? 'Start…' : 'Open…'}
                    </MiniButton>
                  ) : event.status === 'closed' ? (
                    <Link
                      href={`/admin/results?event=${event.id}`}
                      className="inline-flex min-h-11 items-center gap-1 rounded-full px-3 text-sm font-semibold text-primary hover:bg-primary-wash"
                      aria-label={`Results for ${event.name}`}
                    >
                      Results
                    </Link>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </Panel>
  )
}
