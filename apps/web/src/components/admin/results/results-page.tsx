'use client'

import { Download, EyeOff, Lock, Megaphone, Scale, Trophy } from 'lucide-react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'
import { plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { BallotResult, EventResults, ResultsResponse } from '@/lib/admin/types'
import { formatWhen } from '@/lib/admin/zoned'
import { ConfirmDialog } from '../confirm'
import { SelectField } from '../form'
import { Bar, EmptyState, FlashRegion, LoadError, PageHeader, PageSkeleton, Spinner, StatusChip, useDialog, useFlash } from '../primitives'

const csvHref = (eventId?: string) => `/api/v1/admin/results/export.csv${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ''}`

function defaultEvent(events: EventResults[]): string | null {
  const open = events.find((event) => event.status === 'open')
  if (open) return open.event_id
  const closed = events.filter((event) => event.status === 'closed')
  return closed[closed.length - 1]?.event_id ?? events[0]?.event_id ?? null
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

export function ResultsPage() {
  const { me } = useAdminSession()
  const router = useRouter()
  const params = useSearchParams()
  const all = useAdminQuery<ResultsResponse>('/admin/results')
  const [chosen, setChosen] = useState<string | null>(params.get('event'))
  const events = all.data?.events ?? []
  const selectedId = chosen && events.some((event) => event.event_id === chosen) ? chosen : defaultEvent(events)
  const one = useAdminQuery<ResultsResponse>(selectedId ? '/admin/results' : null, selectedId ? { event_id: selectedId } : undefined)
  const flash = useFlash()
  const dialog = useDialog<{ kind: 'publish' | 'unpublish'; event: EventResults }>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Live tallies: at most one refetch every 1.5 s, and only for the event on screen.
  useAdminSignal((signal) => {
    if (signal.type === 'tally') {
      if (!signal.events || (selectedId && signal.events.includes(selectedId))) one.reloadSoon(1500)
    } else if (signal.type !== 'email' && signal.type !== 'revoked') {
      void all.reload()
      void one.reload()
    }
  })

  const tz = all.data?.timezone ?? me.programme.timezone
  const fresh = one.data?.events[0]
  const event = fresh && fresh.event_id === selectedId ? fresh : events.find((item) => item.event_id === selectedId)

  const select = (id: string) => {
    setChosen(id)
    router.replace(`/admin/results?event=${encodeURIComponent(id)}`, { scroll: false })
  }

  const setPublished = async (target: EventResults, publish: boolean) => {
    setBusy(true)
    setError(null)
    try {
      await adminAction(`/admin/events/${target.event_id}/results/${publish ? 'publish' : 'unpublish'}`)
      dialog.close()
      flash.show({
        tone: 'success',
        message: publish
          ? `Results for ${target.name} are published. Participants can see them now.`
          : `Results for ${target.name} are hidden from participants again.`,
      })
      void all.reload()
      void one.reload()
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Results"
        description="Vote counts for each ballot. Participants see them only after you publish."
        actions={
          <a
            href={csvHref()}
            download
            className="inline-flex min-h-11 items-center gap-2 rounded-full border border-plum-faint/60 bg-paper px-4 text-[0.95rem] font-semibold text-plum hover:border-plum-soft hover:bg-white"
          >
            <Download aria-hidden className="size-4" />
            CSV for all events
          </a>
        }
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />

      <div className="mt-6">
        {!all.data ? (
          all.error ? (
            <LoadError error={all.error} onRetry={() => void all.reload()} what="results" />
          ) : (
            <PageSkeleton rows={2} />
          )
        ) : events.length === 0 ? (
          <EmptyState title="No events yet">Results appear here once events are created and voting opens.</EmptyState>
        ) : (
          <>
            <div className="max-w-xl">
              <SelectField label="Event" value={selectedId ?? ''} onChange={(e) => select(e.target.value)}>
                {events.map((item) => (
                  <option key={item.event_id} value={item.event_id}>
                    Round {item.round}: {item.name}
                    {item.status === 'open' ? ' (live)' : item.status === 'closed' ? ' (closed)' : ' (not started)'}
                  </option>
                ))}
              </SelectField>
            </div>
            {event ? (
              <EventResultsView
                event={event}
                timeZone={tz}
                updating={one.pending && one.data !== undefined}
                error={one.error}
                onRetry={() => void one.reload()}
                onPublish={(from) => {
                  setError(null)
                  dialog.open({ kind: event.results_published ? 'unpublish' : 'publish', event }, from)
                }}
              />
            ) : null}
          </>
        )}
      </div>

      {dialog.value ? (
        <ConfirmDialog
          key={dialog.key}
          open
          title={dialog.value.kind === 'publish' ? 'Publish these results?' : 'Unpublish these results?'}
          description={
            dialog.value.kind === 'publish'
              ? `Participants will see the vote counts for every ballot in ${dialog.value.event.name}. You can unpublish them later.`
              : `Participants will no longer see the results of ${dialog.value.event.name}. The votes are not affected.`
          }
          confirmLabel={dialog.value.kind === 'publish' ? 'Publish results' : 'Unpublish results'}
          tone={dialog.value.kind === 'publish' ? 'primary' : 'danger'}
          busy={busy}
          error={error}
          onConfirm={() => dialog.value && void setPublished(dialog.value.event, dialog.value.kind === 'publish')}
          onClose={dialog.close}
        />
      ) : null}
    </>
  )
}

function EventResultsView({
  event,
  timeZone,
  updating,
  error,
  onRetry,
  onPublish,
}: {
  event: EventResults
  timeZone: string
  updating: boolean
  error: unknown
  onRetry: () => void
  onPublish: (from: Element) => void
}) {
  const label =
    event.label === 'final' ? (
      <StatusChip tone="neutral" icon={<Lock aria-hidden className="size-3.5" />} label="Final — voting closed" />
    ) : event.label === 'provisional' ? (
      <StatusChip tone="live" label="Provisional — voting open" />
    ) : (
      <StatusChip tone="neutral" label="Not started" />
    )
  return (
    <section aria-labelledby="results-event" className="mt-6">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-plum-soft">Round {event.round}</p>
          <h2 id="results-event" className="type-display mt-1 break-words text-[1.9rem] text-plum sm:text-[2.3rem]">
            {event.name}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {label}
            {event.results_published ? (
              <StatusChip tone="success" icon={<Megaphone aria-hidden className="size-3.5" />} label="Published to participants" />
            ) : (
              <StatusChip tone="neutral" icon={<EyeOff aria-hidden className="size-3.5" />} label="Not published" />
            )}
            <span aria-live="polite" className="text-sm">
              {updating ? <Spinner label="Updating" /> : null}
            </span>
          </div>
          {event.closed_at ? (
            <p className="mt-2 text-sm text-plum-soft">Voting closed {formatWhen(event.closed_at, timeZone)}.</p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {event.status === 'closed' ? (
            <Button variant={event.results_published ? 'secondary' : 'primary'} onClick={(e) => onPublish(e.currentTarget)}>
              {event.results_published ? <EyeOff aria-hidden className="size-4" /> : <Megaphone aria-hidden className="size-4" />}
              {event.results_published ? 'Unpublish…' : 'Publish results…'}
            </Button>
          ) : null}
          <a
            href={csvHref(event.event_id)}
            download
            className="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-[0.95rem] font-semibold text-primary hover:bg-primary-wash"
          >
            <Download aria-hidden className="size-4" />
            CSV for this event
          </a>
        </div>
      </div>
      {event.status === 'open' ? (
        <p className="mt-3 text-sm text-plum-soft">Counts update as votes arrive. You can publish once voting closes.</p>
      ) : null}
      {error ? (
        <div className="mt-4">
          <LoadError error={error} onRetry={onRetry} what="the latest counts" />
        </div>
      ) : null}
      {event.ballots.length === 0 ? (
        <Notice tone="info" className="mt-5">
          This event has no ballots with nominees yet.
        </Notice>
      ) : (
        <div className="mt-5 grid gap-5 xl:grid-cols-2">
          {event.ballots.map((ballot) => (
            <BallotResultCard key={ballot.ballot_id} ballot={ballot} final={event.label === 'final'} started={event.label !== 'not_started'} />
          ))}
        </div>
      )}
    </section>
  )
}

function BallotResultCard({ ballot, final, started }: { ballot: BallotResult; final: boolean; started: boolean }) {
  const order = new Map(ballot.nominees.map((nominee, index) => [nominee.nominee_id, index]))
  const nominees = [...ballot.nominees].sort(
    (a, b) => b.votes - a.votes || (order.get(a.nominee_id) ?? 0) - (order.get(b.nominee_id) ?? 0),
  )
  const leaders = nominees.filter((nominee) => nominee.leading)
  const top = nominees[0]?.votes ?? 0
  const turnout = ballot.eligible_voters ? Math.round((ballot.total_votes / ballot.eligible_voters) * 100) : 0
  const headingId = `ballot-result-${ballot.ballot_id}`

  let outcome: React.ReactNode
  if (!started) outcome = <p className="text-sm text-plum-soft">Voting has not opened yet.</p>
  else if (ballot.total_votes === 0) outcome = <p className="text-sm text-plum-soft">No votes yet.</p>
  else if (ballot.tie) {
    outcome = (
      <p className="flex items-start gap-2 rounded-2xl bg-gold-wash px-3 py-2.5 text-[0.95rem] font-semibold text-plum">
        <Scale aria-hidden className="mt-0.5 size-4 shrink-0 text-gold-deep" />
        <span>
          Tie: {joinNames(leaders.map((nominee) => nominee.name))}, {plural(top, 'vote')} each.
          {final ? ' There is no single winner.' : ''}
        </span>
      </p>
    )
  } else if (leaders[0]) {
    outcome = (
      <p className="flex items-start gap-2 rounded-2xl bg-ivory px-3 py-2.5 text-[0.95rem] font-semibold text-plum">
        <Trophy aria-hidden className="mt-0.5 size-4 shrink-0 text-gold-deep" />
        <span>
          {final ? 'Winner' : 'Leading so far'}: {leaders[0].name}, {plural(leaders[0].votes, 'vote')}.
        </span>
      </p>
    )
  }

  return (
    <section aria-labelledby={headingId} className="min-w-0 rounded-[var(--radius-card)] border border-sand bg-paper p-4 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 id={headingId} className="break-words text-[1.15rem] font-bold text-plum">
          {ballot.title}
        </h3>
        <span className="text-sm text-plum-soft">{ballot.kind === 'team' ? 'Team ballot' : 'Individual ballot'}</span>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
        <div className="rounded-2xl bg-ivory px-3 py-2">
          <dt className="text-plum-soft">Votes</dt>
          <dd className="tabular text-[1.3rem] font-bold text-plum">{ballot.total_votes.toLocaleString('en-IN')}</dd>
        </div>
        <div className="rounded-2xl bg-ivory px-3 py-2">
          <dt className="text-plum-soft">Eligible</dt>
          <dd className="tabular text-[1.3rem] font-bold text-plum">{ballot.eligible_voters.toLocaleString('en-IN')}</dd>
        </div>
        <div className="rounded-2xl bg-ivory px-3 py-2">
          <dt className="text-plum-soft">Not voted</dt>
          <dd className="tabular text-[1.3rem] font-bold text-plum">{ballot.remaining_voters.toLocaleString('en-IN')}</dd>
        </div>
      </dl>
      <p className="mt-2 text-sm text-plum-soft">{turnout}% of eligible voters took part.</p>
      <div className="mt-4">{outcome}</div>
      <ol className="mt-4 space-y-4">
        {nominees.map((nominee) => {
          const leading = nominee.leading && ballot.total_votes > 0
          return (
            <li key={nominee.nominee_id}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <p className="min-w-0 break-words font-semibold text-plum">
                  {nominee.name}
                  {nominee.affiliation ? <span className="font-normal text-plum-soft"> ({nominee.affiliation})</span> : null}
                  {leading ? (
                    <span className="ml-2 inline-flex items-center gap-1 text-sm font-semibold text-gold-deep">
                      {ballot.tie ? <Scale aria-hidden className="size-3.5" /> : <Trophy aria-hidden className="size-3.5" />}
                      {ballot.tie ? 'Tied' : final ? 'Winner' : 'Leading'}
                    </span>
                  ) : null}
                </p>
                <p className="tabular text-sm text-plum">
                  <strong>{plural(nominee.votes, 'vote')}</strong> <span className="text-plum-soft">({nominee.percent.toFixed(1)}%)</span>
                </p>
              </div>
              <div className="mt-1.5">
                <Bar value={nominee.votes} max={Math.max(ballot.total_votes, 1)} tone={leading ? 'ink' : 'soft'} size="md" />
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
