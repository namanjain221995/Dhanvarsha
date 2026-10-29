'use client'

import { ArrowLeft, Eye, Lock, Plus, RotateCcw } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError, errorCode, fieldErrors } from '@/lib/admin/errors'
import { CLOSE_REASONS, isQuestion, plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { AdminBallot, AdminEvent, AdminNominee, EventDetail, EventsList } from '@/lib/admin/types'
import { formatWhen, isoToZonedInput, zonedInputToIso } from '@/lib/admin/zoned'
import { ConfirmDialog } from '../confirm'
import { ImageField, InputField, TextAreaField, ZonedDateTimeField } from '../form'
import { FlashRegion, LoadError, MiniButton, PageSkeleton, Panel, useDialog, useFlash } from '../primitives'
import { AddBallotForm, BallotCard, type BallotActions } from './ballots'
import { EventStatusChip, PublishedChip, readinessProblems } from './common'
import { EventPreviewDialog } from './preview'

type EditorDialog =
  | { kind: 'preview' }
  | { kind: 'delete-ballot'; ballot: AdminBallot }
  | { kind: 'delete-nominee'; nominee: AdminNominee; ballot: AdminBallot }

export function EventEditor({ eventId }: { eventId: string }) {
  const { me } = useAdminSession()
  const params = useSearchParams()
  const query = useAdminQuery<EventDetail>(`/admin/events/${eventId}`)
  const list = useAdminQuery<EventsList>('/admin/events')
  const flash = useFlash()
  const dialog = useDialog<EditorDialog>()
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [addingBallot, setAddingBallot] = useState(false)

  useAdminSignal((signal) => {
    if (signal.type === 'state' || signal.type === 'resync' || signal.type === 'visible') {
      void query.reload()
      void list.reload()
    }
  })

  const event = query.data?.event
  const tz = query.data?.timezone ?? me.programme.timezone

  if (!event) {
    return (
      <>
        <BackLink />
        <div className="mt-4">
          {query.error ? (
            errorCode(query.error) === 'NOT_FOUND' ? (
              <Notice tone="warning" title="This event does not exist">
                It may have been created in another programme.{' '}
                <Link href="/admin/events" className="font-semibold text-primary underline underline-offset-2">
                  Back to all events
                </Link>
              </Notice>
            ) : (
              <LoadError error={query.error} onRetry={() => void query.reload()} what="the event" />
            )
          ) : (
            <PageSkeleton rows={3} />
          )}
        </div>
      </>
    )
  }

  const active = list.data?.events ?? []
  const roundIndex = active.findIndex((item) => item.id === event.id)
  const structural = !event.frozen && event.status === 'upcoming'
  const problems = readinessProblems(event)
  const serverNow = query.data ? Date.parse(query.data.server_time) : undefined
  const hasQuestions = event.ballots.some((ballot) => isQuestion(ballot.kind))
  const firstBallot = event.ballots[0]
  const createdNote = !firstBallot
    ? 'Event created with no voting. It can open as it is, or add a ballot or question below.'
    : firstBallot.kind === 'text'
      ? 'Event created. Its question needs nothing more, so it can open as it is.'
      : firstBallot.kind === 'mcq'
        ? 'Event created. Add at least two answer options to its question so it can open.'
        : 'Event created. Add nominees to its ballot so it can open.'

  const refresh = async () => {
    await query.reload()
  }
  const actions: BallotActions = {
    refresh,
    announce: (message) => flash.show({ tone: 'success', message }),
    askDeleteBallot: (ballot, from) => {
      setDialogError(null)
      dialog.open({ kind: 'delete-ballot', ballot }, from)
    },
    askDeleteNominee: (nominee, ballot, from) => {
      setDialogError(null)
      dialog.open({ kind: 'delete-nominee', nominee, ballot }, from)
    },
  }

  const remove = async (path: string, message: string) => {
    setBusy(true)
    setDialogError(null)
    try {
      await adminAction(path, { method: 'DELETE' })
      dialog.close()
      await refresh()
      flash.show({ tone: 'success', message })
    } catch (error) {
      setDialogError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <BackLink />
      <header className="mt-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-plum-soft">
            {event.archived ? 'Archived event' : roundIndex >= 0 ? `Round ${roundIndex + 1} of ${active.length}` : 'Event'}
          </p>
          <h1 className="type-display mt-1 break-words text-[2rem] text-plum sm:text-[2.5rem]">{event.name}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <EventStatusChip event={event} />
            {event.frozen ? (
              <span className="inline-flex min-h-7 items-center gap-1.5 rounded-full bg-plum/[0.06] px-2.5 text-[0.8rem] font-semibold text-plum-soft">
                <Lock aria-hidden className="size-3.5" />
                Ballots locked
              </span>
            ) : null}
            {event.results_published ? <PublishedChip /> : null}
          </div>
        </div>
        <Button variant="secondary" onClick={(e) => dialog.open({ kind: 'preview' }, e.currentTarget)}>
          <Eye aria-hidden className="size-4" />
          Preview
        </Button>
      </header>

      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />
      {params.get('created') === '1' && !flash.flash ? (
        <Notice tone="success" className="mt-5">
          {createdNote}
        </Notice>
      ) : null}

      <div className="mt-5 space-y-3">
        {event.frozen ? (
          <Notice tone="info" title="Voting has opened, so ballots and nominees are locked">
            You can still edit the event’s name, description, rules and logo, and each nominee’s affiliation, team members,
            description and photo. Ballot titles and nominee names accept small spelling corrections with a reason. Nothing can be added or
            removed: a different line-up needs a new event.
          </Notice>
        ) : null}
        {problems.length ? (
          <Notice tone="warning" title="Not ready to open yet">
            {problems.join('. ')}.
          </Notice>
        ) : null}
        {event.status !== 'upcoming' ? (
          <p className="text-sm text-plum-soft">
            Opened {formatWhen(event.opened_at, tz, serverNow)} {event.opened_by === 'schedule' ? 'by the schedule' : 'by an organiser'}.
            {event.status === 'closed'
              ? ` ${event.close_reason ? (CLOSE_REASONS[event.close_reason] ?? 'Closed') : 'Closed'}, ${formatWhen(event.closed_at, tz, serverNow)}.`
              : ` Voting closes ${formatWhen(event.voting_deadline_at, tz, serverNow)}.`}{' '}
            {hasQuestions ? plural(event.vote_count, 'vote or answer', 'votes and answers') : plural(event.vote_count, 'vote')} so far.
          </p>
        ) : null}
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] xl:items-start">
        <div className="min-w-0 space-y-5">
          <h2 className="type-display text-[1.6rem] text-plum">Ballots</h2>
          {event.ballots.length === 0 ? (
            <p className="rounded-[var(--radius-card)] border border-dashed border-sand-strong bg-paper px-4 py-4 text-sm text-plum sm:px-6">
              No voting in this round (show only). While it is live, participants see “No voting in this round. Enjoy the
              show!”.
            </p>
          ) : null}
          {event.ballots.map((ballot, index) => (
            <BallotCard key={ballot.id} event={event} ballot={ballot} index={index} actions={actions} />
          ))}
          {structural ? (
            addingBallot ? (
              <AddBallotForm
                event={event}
                onCancel={() => setAddingBallot(false)}
                onSaved={async (message) => {
                  await refresh()
                  setAddingBallot(false)
                  flash.show({ tone: 'success', message })
                }}
              />
            ) : (
              <Button variant="secondary" onClick={() => setAddingBallot(true)}>
                <Plus aria-hidden className="size-4" />
                {event.ballots.length ? 'Add another ballot' : 'Add a ballot or question'}
              </Button>
            )
          ) : (
            <p className="text-sm text-plum-soft">
              Ballots cannot be added or removed after voting opens. Create a new event for a different question.
            </p>
          )}
        </div>
        <div className="min-w-0 space-y-5">
          <DetailsForm key={`details-${event.id}`} event={event} onSaved={(updated, message) => {
            query.mutate((current) => ({ ...current, event: { ...updated, round: current.event.round } }))
            flash.show({ tone: 'success', message })
          }} onReload={refresh} />
          <ScheduleForm key={`schedule-${event.id}`} event={event} timeZone={tz} runMode={list.data?.run_mode} onSaved={(updated, message) => {
            query.mutate((current) => ({ ...current, event: { ...updated, round: current.event.round } }))
            flash.show({ tone: 'success', message })
          }} onReload={refresh} />
        </div>
      </div>

      {dialog.value?.kind === 'preview' ? <EventPreviewDialog key={dialog.key} eventId={event.id} onClose={dialog.close} /> : null}
      {dialog.value?.kind === 'delete-ballot' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          tone="danger"
          title={isQuestion(dialog.value.ballot.kind) ? 'Remove this question?' : 'Remove this ballot?'}
          description={
            dialog.value.ballot.kind === 'text' || !dialog.value.ballot.nominees.length
              ? `“${dialog.value.ballot.title}” will be deleted. This cannot be undone.`
              : `“${dialog.value.ballot.title}” and its ${plural(
                  dialog.value.ballot.nominees.length,
                  dialog.value.ballot.kind === 'mcq' ? 'answer option' : 'nominee',
                )} will be deleted. This cannot be undone.`
          }
          confirmLabel={isQuestion(dialog.value.ballot.kind) ? 'Remove question' : 'Remove ballot'}
          busy={busy}
          error={dialogError}
          onConfirm={() =>
            dialog.value?.kind === 'delete-ballot' &&
            void remove(
              `/admin/ballots/${dialog.value.ballot.id}`,
              `${isQuestion(dialog.value.ballot.kind) ? 'Question' : 'Ballot'} “${dialog.value.ballot.title}” removed.`,
            )
          }
          onClose={dialog.close}
        />
      ) : null}
      {dialog.value?.kind === 'delete-nominee' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          tone="danger"
          title={`Remove ${dialog.value.nominee.name}?`}
          description={
            dialog.value.ballot.kind === 'mcq'
              ? `This answer option will no longer appear on “${dialog.value.ballot.title}”. This cannot be undone.`
              : `They will no longer appear on “${dialog.value.ballot.title}”. This cannot be undone.`
          }
          confirmLabel={dialog.value.ballot.kind === 'mcq' ? 'Remove option' : 'Remove nominee'}
          busy={busy}
          error={dialogError}
          onConfirm={() =>
            dialog.value?.kind === 'delete-nominee' &&
            void remove(`/admin/nominees/${dialog.value.nominee.id}`, `${dialog.value.nominee.name} removed.`)
          }
          onClose={dialog.close}
        />
      ) : null}
    </>
  )
}

function BackLink() {
  return (
    <Link
      href="/admin/events"
      className="-ml-3 inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-sm font-semibold text-plum-soft hover:bg-plum/5 hover:text-plum"
    >
      <ArrowLeft aria-hidden className="size-4" />
      All events
    </Link>
  )
}

type Details = { name: string; description: string; rules: string; logo: { id: string | null; url: string | null } }
const pickDetails = (e: AdminEvent): Details => ({
  name: e.name,
  description: e.description,
  rules: e.rules,
  logo: { id: e.logo_asset_id, url: e.logo_url },
})
const sameDetails = (a: Details, b: Details) =>
  a.name.trim() === b.name && a.description.trim() === b.description && a.rules.trim() === b.rules && a.logo.id === b.logo.id

function StaleNotice({ onReload }: { onReload: () => void }) {
  return (
    <Notice
      tone="warning"
      role="alert"
      title="Someone else changed this event"
      action={
        <MiniButton onClick={onReload}>
          <RotateCcw aria-hidden className="size-4" />
          Load the latest version
        </MiniButton>
      }
    >
      Your changes here were not saved. Load the latest version, then make your change again.
    </Notice>
  )
}

function DetailsForm({
  event,
  onSaved,
  onReload,
}: {
  event: AdminEvent
  onSaved: (event: AdminEvent, message: string) => void
  onReload: () => Promise<void>
}) {
  const [draft, setDraft] = useState<Details>(() => pickDetails(event))
  const [base, setBase] = useState(event)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const dirty = !sameDetails(draft, pickDetails(base))
  if (event !== base) {
    // Adopt new server data when nothing is being edited, or when only other parts changed.
    if (!dirty) {
      setBase(event)
      setDraft(pickDetails(event))
    } else if (sameDetails(pickDetails(event), pickDetails(base)) && event.version !== base.version) {
      setBase(event)
    }
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!draft.name.trim()) {
      setErrors({ name: 'Give the event a name.' })
      return
    }
    const body: Record<string, unknown> = { version: base.version }
    const original = pickDetails(base)
    if (draft.name.trim() !== original.name) body.name = draft.name.trim()
    if (draft.description.trim() !== original.description) body.description = draft.description.trim()
    if (draft.rules.trim() !== original.rules) body.rules = draft.rules.trim()
    if (draft.logo.id !== original.logo.id) {
      if (draft.logo.id) body.logo_asset_id = draft.logo.id
      else body.clear_logo = true
    }
    setBusy(true)
    setErrors({})
    setError(null)
    try {
      const result = await adminAction<{ event: AdminEvent }>(`/admin/events/${event.id}`, { method: 'PATCH', body })
      setBase(result.event)
      setDraft(pickDetails(result.event))
      onSaved(result.event, 'Event details saved.')
    } catch (failure) {
      if (errorCode(failure) === 'STALE_EVENT') setStale(true)
      else {
        const fields = fieldErrors(failure)
        if (Object.keys(fields).length) setErrors(fields)
        else setError(describeError(failure))
      }
    } finally {
      setBusy(false)
    }
  }

  const reload = async () => {
    await onReload()
    setStale(false)
    setDraft(pickDetails(event))
    setBase(event)
  }

  return (
    <Panel
      title="Event details"
      description={event.frozen ? 'Edits after opening are recorded in the audit log.' : 'Shown to participants on the event card.'}
    >
      <form onSubmit={(e) => void save(e)} noValidate className="space-y-5">
        {stale ? <StaleNotice onReload={() => void reload()} /> : null}
        <InputField
          label="Name"
          value={draft.name}
          maxLength={120}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          error={errors.name}
        />
        <TextAreaField
          label="Description"
          optional
          rows={3}
          maxLength={4000}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          error={errors.description}
        />
        <TextAreaField
          label="Rules"
          optional
          rows={3}
          maxLength={4000}
          value={draft.rules}
          onChange={(e) => setDraft({ ...draft, rules: e.target.value })}
          hint="For example: one vote per ballot; votes are final once submitted."
          error={errors.rules}
        />
        <ImageField
          label="Event logo"
          kind="event_logo"
          url={draft.logo.url}
          name={draft.name}
          onUploaded={(asset) => setDraft({ ...draft, logo: { id: asset.id, url: asset.url } })}
          onRemove={() => setDraft({ ...draft, logo: { id: null, url: null } })}
        />
        {error ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" busy={busy} disabled={!dirty || stale}>
            Save details
          </Button>
          {dirty ? (
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setDraft(pickDetails(base))
                setErrors({})
                setError(null)
              }}
            >
              Discard changes
            </Button>
          ) : null}
        </div>
      </form>
    </Panel>
  )
}

function ScheduleForm({
  event,
  timeZone,
  runMode,
  onSaved,
  onReload,
}: {
  event: AdminEvent
  timeZone: string
  runMode: 'manual' | 'scheduled' | undefined
  onSaved: (event: AdminEvent, message: string) => void
  onReload: () => Promise<void>
}) {
  const pick = (e: AdminEvent) => ({
    start: isoToZonedInput(e.planned_start_at, timeZone),
    end: isoToZonedInput(e.planned_end_at, timeZone),
  })
  const [draft, setDraft] = useState(() => pick(event))
  const [base, setBase] = useState(event)
  const [busy, setBusy] = useState<'save' | 'clear' | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [stale, setStale] = useState(false)
  const original = pick(base)
  const dirty = draft.start !== original.start || draft.end !== original.end
  if (event !== base) {
    const fresh = pick(event)
    if (!dirty) {
      setBase(event)
      setDraft(fresh)
    } else if (fresh.start === original.start && fresh.end === original.end && event.version !== base.version) {
      setBase(event)
    }
  }
  const editable = event.status === 'upcoming'
  const hasSchedule = Boolean(base.planned_start_at)

  const submit = async (clear: boolean) => {
    const local: Record<string, string> = {}
    let start: string | null = null
    let end: string | null = null
    if (!clear) {
      start = draft.start ? zonedInputToIso(draft.start, timeZone) : null
      end = draft.end ? zonedInputToIso(draft.end, timeZone) : null
      if (!start && !end) {
        clear = true
      } else {
        if (!start) local.start = 'Enter the planned start, or clear both times.'
        if (!end) local.end = 'Enter the planned end, or clear both times.'
        if (start && end && Date.parse(end) <= Date.parse(start)) local.end = 'The end must be after the start.'
      }
    }
    setErrors(local)
    if (Object.keys(local).length) return
    setBusy(clear ? 'clear' : 'save')
    try {
      const body = clear
        ? { version: base.version, clear_schedule: true }
        : { version: base.version, planned_start_at: start, planned_end_at: end }
      const result = await adminAction<{ event: AdminEvent }>(`/admin/events/${event.id}`, { method: 'PATCH', body })
      setBase(result.event)
      setDraft(pick(result.event))
      onSaved(result.event, clear ? 'Planned times cleared.' : 'Planned times saved.')
    } catch (failure) {
      if (errorCode(failure) === 'STALE_EVENT') setStale(true)
      else {
        const message = describeError(failure)
        setErrors(/end/i.test(message) && !/overlap/i.test(message) ? { end: message } : { start: message })
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel
      title="Planned times"
      description={
        runMode === 'scheduled'
          ? 'In scheduled mode the event opens at the start and closes at the end.'
          : 'Used only in scheduled mode. In manual mode they are just a guide.'
      }
    >
      {!editable ? (
        <p className="text-sm text-plum-soft">
          {hasSchedule
            ? `Planned ${formatWhen(base.planned_start_at, timeZone)} to ${formatWhen(base.planned_end_at, timeZone)}. `
            : 'No planned times. '}
          The schedule of an event that has opened cannot change.
        </p>
      ) : (
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            void submit(false)
          }}
          className="space-y-4"
        >
          {stale ? (
            <StaleNotice
              onReload={() =>
                void onReload().then(() => {
                  setStale(false)
                  setBase(event)
                  setDraft(pick(event))
                })
              }
            />
          ) : null}
          <ZonedDateTimeField label="Planned start" value={draft.start} onChange={(start) => setDraft({ ...draft, start })} timeZone={timeZone} error={errors.start} />
          <ZonedDateTimeField label="Planned end" value={draft.end} onChange={(end) => setDraft({ ...draft, end })} timeZone={timeZone} error={errors.end} />
          <p className="text-sm text-plum-soft">Windows must fall inside programme access and cannot overlap other events.</p>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" busy={busy === 'save'} disabled={!dirty || busy !== null || stale}>
              Save times
            </Button>
            {hasSchedule ? (
              <Button type="button" variant="secondary" busy={busy === 'clear'} disabled={busy !== null || stale} onClick={() => void submit(true)}>
                Clear times
              </Button>
            ) : null}
          </div>
        </form>
      )}
    </Panel>
  )
}
