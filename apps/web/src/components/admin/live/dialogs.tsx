'use client'

import { ArrowRight, CalendarClock, CircleSlash, Clock3, Play, Square } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/ui/misc'
import { ballotTurnout, isQuestion } from '@/lib/admin/labels'
import type { LiveState, SchedulePreview } from '@/lib/admin/types'
import { formatWhen } from '@/lib/admin/zoned'
import { serverClock, useServerNow } from '@/lib/time'
import { ConfirmDialog } from '../confirm'
import { CheckboxField, SelectField } from '../form'
import { OrganiserNote } from '../organiser-note'
import { DeadlinePicker, initialDeadline, resolveDeadline, type DeadlineChoice } from './deadline'

export type LiveBody = Record<string, unknown>

type BaseProps = {
  state: LiveState
  timeZone: string
  busy: boolean
  error: React.ReactNode
  onSubmit: (body: LiveBody) => void
  onClose: () => void
}

export function accessPhase(
  programme: { login_opens_at: string | null; login_closes_at: string | null },
  nowMs: number,
): 'before_open' | 'open' | 'closed' {
  if (!programme.login_opens_at || nowMs < Date.parse(programme.login_opens_at)) return 'before_open'
  if (programme.login_closes_at && nowMs >= Date.parse(programme.login_closes_at)) return 'closed'
  return 'open'
}

function ScheduleNote({ state }: { state: LiveState }) {
  if (state.programme.run_mode !== 'scheduled' || state.programme.schedule_suspended) return null
  return (
    <Notice tone="warning" className="mt-4">
      Doing this by hand pauses the schedule (manual override). Scheduled events will not open on their own until you
      resume the schedule.
    </Notice>
  )
}

function useOpenBlocker(state: LiveState, timeZone: string): string | null {
  const now = useServerNow(5000)
  if (state.programme.ended_at) return 'The programme has ended. Resume the programme before opening events.'
  const phase = accessPhase(state.programme, now)
  if (phase === 'before_open') {
    return state.programme.login_opens_at
      ? `Programme access opens at ${formatWhen(state.programme.login_opens_at, timeZone, now)}. Voting can only open while access is open. Change the access window in Programme settings to open earlier.`
      : 'Programme access has no opening time. Set the access window in Programme settings first.'
  }
  if (phase === 'closed') {
    return 'Programme access has closed. Extend the access window in Programme settings before opening another event.'
  }
  return null
}

function EventSelect({
  state,
  value,
  onChange,
  label,
}: {
  state: LiveState
  value: string
  onChange: (id: string) => void
  label: string
}) {
  const upcoming = state.events.filter((event) => event.status === 'upcoming')
  return (
    <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {upcoming.map((event) => (
        <option key={event.id} value={event.id}>
          Round {event.round}: {event.name}
          {state.next_event?.id === event.id ? ' (up next)' : ''}
        </option>
      ))}
    </SelectField>
  )
}

function useDeadlineState(state: LiveState, timeZone: string, modes: Parameters<typeof initialDeadline>[0]['modes'], currentIso?: string | null) {
  return useState<DeadlineChoice>(() =>
    initialDeadline({ modes, accessCloseIso: state.programme.login_closes_at, timeZone, nowMs: serverClock.now(), currentIso }),
  )
}

export function OpenEventDialog({ state, timeZone, busy, error, onSubmit, onClose, initialEventId }: BaseProps & { initialEventId?: string }) {
  const upcoming = state.events.filter((event) => event.status === 'upcoming')
  const [eventId, setEventId] = useState(initialEventId ?? state.next_event?.id ?? upcoming[0]?.id ?? '')
  const [choice, setChoice] = useDeadlineState(state, timeZone, ['access', 'in', 'at'])
  const [localError, setLocalError] = useState<string | null>(null)
  const blocker = useOpenBlocker(state, timeZone)
  const accessClose = state.programme.login_closes_at
  const selected = upcoming.find((event) => event.id === eventId)

  const submit = () => {
    const result = resolveDeadline(choice, { nowMs: serverClock.now(), timeZone, accessCloseIso: accessClose })
    if (result.error) {
      setLocalError(result.error)
      return
    }
    setLocalError(null)
    onSubmit({ event_id: eventId, ...(result.iso ? { deadline_at: result.iso } : {}) })
  }

  return (
    <ConfirmDialog
      open
      wide
      title="Open voting"
      description="Participants can vote on the event as soon as it opens. Its ballots and nominees lock at that moment."
      confirmLabel="Open voting"
      busy={busy}
      confirmDisabled={!eventId || Boolean(blocker)}
      error={localError ?? error}
      onConfirm={submit}
      onClose={onClose}
    >
      {blocker ? (
        <Notice tone="warning" title="Voting cannot open right now">
          {blocker}
        </Notice>
      ) : null}
      {upcoming.length === 0 ? (
        <Notice tone="info">There are no upcoming events. Create one on the Events page.</Notice>
      ) : (
        <div className="space-y-5">
          <EventSelect state={state} value={eventId} onChange={setEventId} label="Event to open" />
          {selected ? <OrganiserNote note={selected.organiser_note} eventName={selected.name} className="-mt-2" /> : null}
          <DeadlinePicker
            value={choice}
            onChange={(next) => {
              setChoice(next)
              setLocalError(null)
            }}
            modes={['access', 'in', 'at']}
            timeZone={timeZone}
            accessCloseIso={accessClose}
          />
        </div>
      )}
      <ScheduleNote state={state} />
    </ConfirmDialog>
  )
}

export function StartNextDialog({ state, timeZone, busy, error, onSubmit, onClose, initialEventId }: BaseProps & { initialEventId?: string }) {
  const current = state.current_event
  const upcoming = state.events.filter((event) => event.status === 'upcoming')
  const [eventId, setEventId] = useState(initialEventId ?? state.next_event?.id ?? upcoming[0]?.id ?? '')
  const [choice, setChoice] = useDeadlineState(state, timeZone, ['access', 'in', 'at'])
  const [localError, setLocalError] = useState<string | null>(null)
  const blocker = useOpenBlocker(state, timeZone)
  const accessClose = state.programme.login_closes_at
  const selected = upcoming.find((event) => event.id === eventId)

  const submit = () => {
    const result = resolveDeadline(choice, { nowMs: serverClock.now(), timeZone, accessCloseIso: accessClose })
    if (result.error) {
      setLocalError(result.error)
      return
    }
    setLocalError(null)
    onSubmit({ event_id: eventId, ...(result.iso ? { deadline_at: result.iso } : {}) })
  }

  return (
    <ConfirmDialog
      open
      wide
      title="Start the next event"
      description={
        current ? (
          <>
            This closes <strong className="text-plum">{current.name}</strong> and opens the event you choose in one step.
            Votes already cast on {current.name} are final.
          </>
        ) : (
          'Opens the event you choose.'
        )
      }
      confirmLabel="Start next event"
      busy={busy}
      confirmDisabled={!eventId || Boolean(blocker)}
      error={localError ?? error}
      onConfirm={submit}
      onClose={onClose}
    >
      {blocker ? (
        <Notice tone="warning" title="The next event cannot open right now">
          {blocker}
        </Notice>
      ) : null}
      {upcoming.length === 0 ? (
        <Notice tone="info">There are no upcoming events to start. Use Close voting instead.</Notice>
      ) : (
        <div className="space-y-5">
          <EventSelect state={state} value={eventId} onChange={setEventId} label="Event to open" />
          {selected ? <OrganiserNote note={selected.organiser_note} eventName={selected.name} className="-mt-2" /> : null}
          <div className="grid items-center gap-2 rounded-2xl border border-sand bg-ivory p-4 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-sm text-plum-soft">
                <Square aria-hidden className="size-3.5" />
                Closes now
              </p>
              <p className="break-words font-semibold text-plum">{current?.name ?? 'Nothing is open'}</p>
            </div>
            <ArrowRight aria-hidden className="hidden size-5 text-plum-soft sm:block" />
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-sm text-plum-soft">
                <Play aria-hidden className="size-3.5" />
                Opens now
              </p>
              <p className="break-words font-semibold text-plum">{selected?.name ?? 'Choose an event'}</p>
            </div>
          </div>
          <DeadlinePicker
            value={choice}
            onChange={(next) => {
              setChoice(next)
              setLocalError(null)
            }}
            modes={['access', 'in', 'at']}
            timeZone={timeZone}
            accessCloseIso={accessClose}
            legend={`Voting deadline for ${selected?.name ?? 'the next event'}`}
          />
        </div>
      )}
      <ScheduleNote state={state} />
    </ConfirmDialog>
  )
}

export function CloseVotingDialog({ state, busy, error, onSubmit, onClose }: BaseProps) {
  const current = state.current_event
  if (!current) return null
  const ballots = current.results ?? []
  return (
    <ConfirmDialog
      open
      tone="danger"
      title="Close voting?"
      description={
        <>
          Voting for <strong className="text-plum">{current.name}</strong> ends immediately for everyone. Votes already cast
          are final, and a closed event never reopens.
        </>
      }
      confirmLabel="Close voting"
      busy={busy}
      error={error}
      onConfirm={() => onSubmit({ event_id: current.id })}
      onClose={onClose}
    >
      {ballots.length ? (
        <ul className="space-y-1.5 rounded-2xl border border-sand bg-ivory p-4 text-sm">
          {ballots.map((ballot) => {
            const { total, eligible, remaining } = ballotTurnout(ballot)
            const answered = isQuestion(ballot.kind)
            return (
              <li key={ballot.ballot_id} className="flex flex-wrap justify-between gap-x-3">
                <span className="font-semibold text-plum">{ballot.title}</span>
                <span className="tabular text-plum-soft">
                  {total} of {eligible} {answered ? 'answered' : 'voted'}, {remaining} still to {answered ? 'answer' : 'vote'}
                </span>
              </li>
            )
          })}
        </ul>
      ) : null}
      <ScheduleNote state={state} />
    </ConfirmDialog>
  )
}

export function ChangeDeadlineDialog({ state, timeZone, busy, error, onSubmit, onClose }: BaseProps) {
  const current = state.current_event
  const deadline = current?.voting_deadline_at ?? null
  const [choice, setChoice] = useDeadlineState(state, timeZone, ['extend', 'in', 'at', 'access'], deadline)
  const [localError, setLocalError] = useState<string | null>(null)
  const now = useServerNow(5000)
  if (!current) return null
  const accessClose = state.programme.login_closes_at

  const submit = () => {
    const result = resolveDeadline(choice, {
      nowMs: serverClock.now(),
      timeZone,
      accessCloseIso: accessClose,
      currentIso: deadline,
      explicitAccess: true,
    })
    if (result.error || !result.iso) {
      setLocalError(result.error ?? 'Choose a deadline.')
      return
    }
    setLocalError(null)
    onSubmit({ event_id: current.id, deadline_at: result.iso })
  }

  return (
    <ConfirmDialog
      open
      wide
      title="Change the deadline"
      description={
        <>
          {current.name} currently closes at{' '}
          <strong className="text-plum">{deadline ? formatWhen(deadline, timeZone, now) : 'no set time'}</strong>.
          Participants see the new deadline straight away.
        </>
      }
      confirmLabel="Change deadline"
      busy={busy}
      error={localError ?? error}
      onConfirm={submit}
      onClose={onClose}
    >
      <DeadlinePicker
        value={choice}
        onChange={(next) => {
          setChoice(next)
          setLocalError(null)
        }}
        modes={['extend', 'in', 'at', 'access']}
        timeZone={timeZone}
        accessCloseIso={accessClose}
        currentIso={deadline}
        legend="New deadline"
      />
      <ScheduleNote state={state} />
    </ConfirmDialog>
  )
}

export function EndProgrammeDialog({ state, busy, error, onSubmit, onClose }: BaseProps) {
  const current = state.current_event
  return (
    <ConfirmDialog
      open
      tone="danger"
      title="End the programme?"
      description="Use this when the show is over. No event can open after this, including scheduled ones, until you resume the programme."
      confirmLabel="End programme"
      busy={busy}
      error={error}
      onConfirm={() => onSubmit({})}
      onClose={onClose}
    >
      {current ? (
        <Notice tone="warning" title={`Voting for ${current.name} closes now`}>
          Votes already cast are final, and the event cannot reopen.
        </Notice>
      ) : (
        <p className="text-sm text-plum-soft">No event is open, so no votes are affected.</p>
      )}
    </ConfirmDialog>
  )
}

export function ResumeProgrammeDialog({ state, busy, error, onSubmit, onClose }: BaseProps) {
  const scheduled = state.programme.run_mode === 'scheduled'
  return (
    <ConfirmDialog
      open
      title="Resume the programme?"
      description="Organisers can open events again. Events that already closed stay closed."
      confirmLabel="Resume programme"
      busy={busy}
      error={error}
      onConfirm={() => onSubmit({})}
      onClose={onClose}
    >
      {scheduled ? (
        <p className="text-sm text-plum-soft">
          The schedule stays paused after this. Resume the schedule separately when you want it to open events again.
        </p>
      ) : null}
    </ConfirmDialog>
  )
}

const OUTCOME: Record<SchedulePreview['plan'][number]['outcome'], { icon: React.ReactNode; tone: string }> = {
  opens_now: { icon: <Play aria-hidden className="size-4" />, tone: 'text-plum' },
  opens_later: { icon: <CalendarClock aria-hidden className="size-4" />, tone: 'text-plum' },
  window_passed: { icon: <CircleSlash aria-hidden className="size-4" />, tone: 'text-gold-deep' },
  closes_when_next_opens: { icon: <Square aria-hidden className="size-4" />, tone: 'text-plum' },
  stays_open_until_deadline: { icon: <Clock3 aria-hidden className="size-4" />, tone: 'text-plum' },
}

export function ResumeScheduleDialog({
  preview,
  timeZone,
  busy,
  error,
  onSubmit,
  onClose,
}: Omit<BaseProps, 'state'> & { preview: SchedulePreview }) {
  const now = Date.parse(preview.server_time)
  const immediate = preview.plan.find((item) => item.outcome === 'opens_now')
  const describe = (item: SchedulePreview['plan'][number]) => {
    switch (item.outcome) {
      case 'opens_now':
        return `Opens immediately. Its planned window ends ${formatWhen(item.planned_end_at, timeZone, now)}.`
      case 'opens_later':
        return `Opens at ${formatWhen(item.planned_start_at, timeZone, now)} and closes at ${formatWhen(item.planned_end_at, timeZone, now)}.`
      case 'window_passed':
        return `Will not open: its planned window ended ${formatWhen(item.planned_end_at, timeZone, now)}. Open it by hand or give it new times.`
      case 'closes_when_next_opens':
        return `Live now. Closes when ${immediate?.name ?? 'the next scheduled event'} opens.`
      case 'stays_open_until_deadline':
        return `Live now. Stays open until its deadline, ${formatWhen(item.planned_end_at, timeZone, now)}.`
    }
  }
  return (
    <ConfirmDialog
      open
      wide
      title="Resume the schedule?"
      description="The schedule takes over again from now. This is what it will do:"
      confirmLabel="Resume schedule"
      busy={busy}
      error={error}
      onConfirm={() => onSubmit({ expected_live_version: preview.live_version })}
      onClose={onClose}
    >
      {preview.plan.length === 0 ? (
        <Notice tone="info">
          No upcoming event has planned start and end times, so nothing will open automatically. Add planned times on the
          Events page.
        </Notice>
      ) : (
        <ol className="divide-y divide-sand rounded-2xl border border-sand bg-ivory">
          {preview.plan.map((item) => (
            <li key={item.event_id} className="flex gap-3 px-4 py-3">
              <span className={`mt-0.5 ${OUTCOME[item.outcome].tone}`}>{OUTCOME[item.outcome].icon}</span>
              <div className="min-w-0">
                <p className="break-words font-semibold text-plum">{item.name}</p>
                <p className={`text-sm ${item.outcome === 'window_passed' ? 'text-gold-deep' : 'text-plum-soft'}`}>
                  {describe(item)}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </ConfirmDialog>
  )
}

export function RestartProgrammeDialog({ state, busy, error, onSubmit, onClose }: BaseProps) {
  const [understood, setUnderstood] = useState(false)
  const current = state.current_event
  const rounds = state.events.length
  return (
    <ConfirmDialog
      open
      tone="danger"
      title="Restart from round 1?"
      description={`All ${rounds} rounds go back to the start, exactly as they are now: same names, ballots, teams and members. Nothing opens by itself; you open round 1 when you are ready.`}
      confirmLabel="Restart from round 1"
      confirmDisabled={!understood}
      busy={busy}
      error={error}
      onConfirm={() => onSubmit({})}
      onClose={onClose}
    >
      <ul className="list-disc space-y-1.5 pl-5 text-sm text-plum">
        {current ? <li>Voting for <strong>{current.name}</strong> closes now.</li> : null}
        <li>Everyone can vote again in every round. Participants stay signed in.</li>
        <li>
          Votes cast so far are kept safely, but they no longer count: they stay with the old rounds, which are archived
          (Events › show archived) and hidden from participants and results.
        </li>
        <li>Planned times that are already in the past are cleared.</li>
      </ul>
      <CheckboxField
        label="I understand that the results so far will no longer count"
        checked={understood}
        onChange={setUnderstood}
      />
    </ConfirmDialog>
  )
}
