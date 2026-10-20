'use client'

import { Archive, ArchiveRestore, ArrowDown, ArrowUp, CalendarPlus, Pencil, Plus } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError, fieldErrors } from '@/lib/admin/errors'
import { plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { AdminEvent, BallotKind, EventsList } from '@/lib/admin/types'
import { formatWhen, zonedInputToIso } from '@/lib/admin/zoned'
import { ConfirmDialog } from '../confirm'
import { InputField, RadioCards, ZonedDateTimeField } from '../form'
import {
  EmptyState,
  FlashRegion,
  IconButton,
  LoadError,
  MiniButton,
  PageHeader,
  PageSkeleton,
  Panel,
  useDialog,
  useFlash,
  focusSoon,
} from '../primitives'
import { EventStatusChip, nomineeCount, PublishedChip, ReadinessChip } from './common'

type EventsDialog = { kind: 'create' } | { kind: 'archive'; event: AdminEvent } | { kind: 'restore'; event: AdminEvent }

export function EventsListPage() {
  const { me } = useAdminSession()
  const query = useAdminQuery<EventsList>('/admin/events', { include_archived: true })
  const flash = useFlash()
  const dialog = useDialog<EventsDialog>()
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [moving, setMoving] = useState<string | null>(null)

  useAdminSignal((signal) => {
    if (signal.type === 'state' || signal.type === 'resync' || signal.type === 'poll' || signal.type === 'visible') {
      void query.reload()
    }
  })

  const data = query.data
  const tz = data?.timezone ?? me.programme.timezone
  const active = (data?.events ?? []).filter((event) => !event.archived)
  const archived = (data?.events ?? []).filter((event) => event.archived)

  const openDialog = (next: EventsDialog, from?: Element | null) => {
    setDialogError(null)
    dialog.open(next, from)
  }

  const move = async (event: AdminEvent, direction: -1 | 1) => {
    const index = active.findIndex((item) => item.id === event.id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= active.length) return
    const order = active.map((item) => item.id)
    order.splice(index, 1)
    order.splice(target, 0, event.id)
    setMoving(event.id)
    query.mutate((current) => ({
      ...current,
      events: [
        ...order.map((id, i) => ({ ...current.events.find((item) => item.id === id)!, position: i + 1 })),
        ...current.events.filter((item) => item.archived),
      ],
    }))
    // Keep focus on the control that moved (the row is re-inserted in its new place).
    window.requestAnimationFrame(() => {
      const preferred = document.getElementById(`move-${direction < 0 ? 'up' : 'down'}-${event.id}`) as HTMLButtonElement | null
      const fallback = document.getElementById(`move-${direction < 0 ? 'down' : 'up'}-${event.id}`) as HTMLButtonElement | null
      ;(preferred && !preferred.disabled ? preferred : fallback)?.focus()
    })
    try {
      await adminAction('/admin/events/reorder', { body: { ordered_ids: order } })
      flash.show({ tone: 'success', message: `${event.name} is now round ${target + 1}.` })
    } catch (error) {
      flash.fail(error, 'The new order was not saved')
    } finally {
      setMoving(null)
      void query.reload()
    }
  }

  const archive = async (event: AdminEvent, restore: boolean) => {
    setBusy(true)
    setDialogError(null)
    try {
      await adminAction(`/admin/events/${event.id}/${restore ? 'unarchive' : 'archive'}`)
      dialog.close()
      flash.show({
        tone: 'success',
        message: restore ? `${event.name} is back in the programme.` : `${event.name} is archived. Its votes and results are kept.`,
      })
      void query.reload()
    } catch (error) {
      setDialogError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Events"
        description="The rounds of the programme, in running order. Open them from Live control."
        actions={
          <Button onClick={(e) => openDialog({ kind: 'create' }, e.currentTarget)}>
            <Plus aria-hidden className="size-4" />
            New event
          </Button>
        }
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />

      <div className="mt-6 space-y-5">
        {!data ? (
          query.error ? (
            <LoadError error={query.error} onRetry={() => void query.reload()} what="the events" />
          ) : (
            <PageSkeleton rows={2} />
          )
        ) : (
          <>
            <Panel
              title={`Running order (${active.length})`}
              description={data.run_mode === 'scheduled' ? 'Scheduled mode: events with planned times open on their own.' : 'Manual mode: events open when an organiser opens them.'}
            >
              {active.length === 0 ? (
                <EmptyState
                  icon={<CalendarPlus aria-hidden className="size-6" />}
                  title="No events yet"
                  action={
                    <Button onClick={(e) => openDialog({ kind: 'create' }, e.currentTarget)}>
                      <Plus aria-hidden className="size-4" />
                      Create the first event
                    </Button>
                  }
                >
                  Each event is one round of the show, with one or more ballots for participants to vote on.
                </EmptyState>
              ) : (
                <ol className="divide-y divide-sand">
                  {active.map((event, index) => (
                    <EventRow
                      key={event.id}
                      event={event}
                      round={index + 1}
                      total={active.length}
                      timeZone={tz}
                      serverNow={Date.parse(data.server_time)}
                      moving={moving !== null}
                      onMove={(direction) => void move(event, direction)}
                      onArchive={(from) => openDialog({ kind: 'archive', event }, from)}
                    />
                  ))}
                </ol>
              )}
            </Panel>

            {archived.length ? (
              <Panel title={`Archived (${archived.length})`} description="Hidden from participants and Live control. Votes and results are kept.">
                <ul className="divide-y divide-sand">
                  {archived.map((event) => (
                    <li key={event.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="break-words font-semibold text-plum">{event.name}</p>
                        <p className="text-sm text-plum-soft">
                          {event.status === 'closed' ? `Closed, ${plural(event.vote_count, 'vote')}.` : 'Never opened.'}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        <Link
                          href={`/admin/events/${event.id}`}
                          className="inline-flex min-h-11 items-center gap-1.5 rounded-full px-3.5 text-sm font-semibold text-plum-soft hover:bg-plum/5 hover:text-plum"
                        >
                          <Pencil aria-hidden className="size-4" />
                          View<span className="sr-only"> {event.name}</span>
                        </Link>
                        <MiniButton
                          aria-label={`Restore ${event.name}…`}
                          onClick={(e) => openDialog({ kind: 'restore', event }, e.currentTarget)}
                        >
                          <ArchiveRestore aria-hidden className="size-4" />
                          Restore…
                        </MiniButton>
                      </div>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}
          </>
        )}
      </div>

      {dialog.value?.kind === 'create' ? (
        <CreateEventDialog key={dialog.key} timeZone={tz} onClose={dialog.close} />
      ) : null}
      {dialog.value?.kind === 'archive' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          title={`Archive ${dialog.value.event.name}?`}
          description="It disappears from the participant programme and from Live control. Its votes and results are kept, and you can restore it later."
          confirmLabel="Archive event"
          busy={busy}
          error={dialogError}
          onConfirm={() => dialog.value?.kind === 'archive' && void archive(dialog.value.event, false)}
          onClose={dialog.close}
        />
      ) : null}
      {dialog.value?.kind === 'restore' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          title={`Restore ${dialog.value.event.name}?`}
          description="It returns to the programme in its previous place. If its planned window now overlaps another event, change one of the schedules first."
          confirmLabel="Restore event"
          busy={busy}
          error={dialogError}
          onConfirm={() => dialog.value?.kind === 'restore' && void archive(dialog.value.event, true)}
          onClose={dialog.close}
        />
      ) : null}
    </>
  )
}

function EventRow({
  event,
  round,
  total,
  timeZone,
  serverNow,
  moving,
  onMove,
  onArchive,
}: {
  event: AdminEvent
  round: number
  total: number
  timeZone: string
  serverNow: number
  moving: boolean
  onMove: (direction: -1 | 1) => void
  onArchive: (from: Element) => void
}) {
  const nominees = nomineeCount(event)
  const kinds = Array.from(new Set(event.ballots.map((ballot) => (ballot.kind === 'team' ? 'team' : 'individual'))))
  const when =
    event.status === 'upcoming'
      ? event.planned_start_at
        ? `Planned ${formatWhen(event.planned_start_at, timeZone, serverNow)} to ${formatWhen(event.planned_end_at, timeZone, serverNow)}`
        : 'No planned time'
      : event.status === 'open'
        ? `Opened ${formatWhen(event.opened_at, timeZone, serverNow)}`
        : `Closed ${formatWhen(event.closed_at, timeZone, serverNow)}`
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 py-4 md:grid-cols-[auto_minmax(0,1fr)_auto] md:items-center">
      <span
        aria-hidden
        className={`tabular flex size-9 items-center justify-center rounded-xl text-sm font-bold ${
          event.status === 'open' ? 'bg-gold-wash text-gold-deep ring-1 ring-gold/50' : 'bg-plum/[0.06] text-plum-soft'
        }`}
      >
        {round}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Link
            href={`/admin/events/${event.id}`}
            className="min-w-0 break-words text-[1.02rem] font-semibold text-plum underline-offset-2 hover:underline"
          >
            <span className="sr-only">Round {round}: </span>
            {event.name}
          </Link>
          <EventStatusChip event={event} />
          <ReadinessChip event={event} />
          {event.results_published ? <PublishedChip /> : null}
        </div>
        <p className="mt-1 text-sm text-plum-soft">
          {plural(event.ballots.length, 'ballot')} ({kinds.join(' and ') || 'none'}), {plural(nominees, 'nominee')}.{' '}
          {when}.{event.vote_count ? ` ${plural(event.vote_count, 'vote')}.` : ''}
        </p>
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-1 md:col-span-1 md:justify-end">
        <IconButton
          id={`move-up-${event.id}`}
          label={`Move ${event.name} up`}
          disabled={round === 1 || moving}
          onClick={() => onMove(-1)}
        >
          <ArrowUp aria-hidden className="size-[1.1rem]" />
        </IconButton>
        <IconButton
          id={`move-down-${event.id}`}
          label={`Move ${event.name} down`}
          disabled={round === total || moving}
          onClick={() => onMove(1)}
        >
          <ArrowDown aria-hidden className="size-[1.1rem]" />
        </IconButton>
        <Link
          href={`/admin/events/${event.id}`}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-plum-faint/60 bg-paper px-3.5 text-sm font-semibold text-plum hover:border-plum-soft hover:bg-white"
        >
          <Pencil aria-hidden className="size-4" />
          Edit<span className="sr-only"> {event.name}</span>
        </Link>
        {event.status !== 'open' ? (
          <MiniButton variant="quiet" aria-label={`Archive ${event.name}…`} onClick={(e) => onArchive(e.currentTarget)}>
            <Archive aria-hidden className="size-4" />
            Archive…
          </MiniButton>
        ) : null}
      </div>
    </li>
  )
}

function CreateEventDialog({ timeZone, onClose }: { timeZone: string; onClose: () => void }) {
  const router = useRouter()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<BallotKind>('team')
  const [ballotTitle, setBallotTitle] = useState('')
  const [withSchedule, setWithSchedule] = useState(false)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => focusSoon(nameRef.current), [])

  const submit = async () => {
    const local: Record<string, string> = {}
    if (!name.trim()) local.name = 'Give the event a name.'
    let startIso: string | null = null
    let endIso: string | null = null
    if (withSchedule) {
      startIso = start ? zonedInputToIso(start, timeZone) : null
      endIso = end ? zonedInputToIso(end, timeZone) : null
      if (!startIso) local.planned_start_at = 'Enter the planned start.'
      if (!endIso) local.planned_end_at = 'Enter the planned end.'
      if (startIso && endIso && Date.parse(endIso) <= Date.parse(startIso)) local.planned_end_at = 'The end must be after the start.'
    }
    setErrors(local)
    if (Object.keys(local).length) return
    setBusy(true)
    setError(null)
    try {
      const result = await adminAction<{ event: AdminEvent }>('/admin/events', {
        body: {
          name: name.trim(),
          ballot_kind: kind,
          ballot_title: ballotTitle.trim() || (kind === 'team' ? 'Best team' : 'Best performer'),
          ...(withSchedule ? { planned_start_at: startIso, planned_end_at: endIso } : {}),
        },
      })
      router.push(`/admin/events/${result.event.id}?created=1`)
    } catch (failure) {
      setBusy(false)
      const fields = fieldErrors(failure)
      if (Object.keys(fields).length) setErrors(fields)
      const message = describeError(failure)
      if (/planned|window|overlaps/i.test(message)) setErrors((prev) => ({ ...prev, planned_start_at: message }))
      else setError(message)
    }
  }

  return (
    <ConfirmDialog
      open
      wide
      title="New event"
      description="Create the round now, then add nominees on the next page."
      confirmLabel="Create event"
      busy={busy}
      error={error}
      onConfirm={() => void submit()}
      onClose={onClose}
    >
      <div className="space-y-5">
        <InputField
          ref={nameRef}
          label="Event name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          error={errors.name}
          placeholder="For example: Lightning Pitches"
        />
        <RadioCards<BallotKind>
          legend="Who participants vote for"
          name="new-ballot-kind"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'team', label: 'Teams', description: 'Each nominee is a team or group.' },
            { value: 'individual', label: 'Individuals', description: 'Each nominee is one person.' },
          ]}
        />
        <InputField
          label="Ballot title"
          optional
          value={ballotTitle}
          maxLength={80}
          onChange={(e) => setBallotTitle(e.target.value)}
          placeholder={kind === 'team' ? 'Best team' : 'Best performer'}
          hint="The question participants answer. You can add more ballots to the event later."
          error={errors.ballot_title}
        />
        <div>
          <label className="flex min-h-11 cursor-pointer items-center gap-3">
            <input
              type="checkbox"
              checked={withSchedule}
              onChange={(e) => setWithSchedule(e.target.checked)}
              className="size-5 accent-plum"
            />
            <span className="font-semibold text-plum">Add planned start and end times</span>
          </label>
          <p className="ml-8 text-sm text-plum-soft">Used to open the event automatically in scheduled mode.</p>
        </div>
        {withSchedule ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <ZonedDateTimeField label="Planned start" value={start} onChange={setStart} timeZone={timeZone} error={errors.planned_start_at} />
            <ZonedDateTimeField label="Planned end" value={end} onChange={setEnd} timeZone={timeZone} error={errors.planned_end_at} />
          </div>
        ) : null}
        {Object.keys(errors).length && !error ? (
          <Notice tone="danger" role="alert">
            Check the highlighted fields.
          </Notice>
        ) : null}
      </div>
    </ConfirmDialog>
  )
}
