'use client'

import { Bot, ScrollText, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AUDIT_PREFIXES, auditActionLabel, humanize, plural } from '@/lib/admin/labels'
import { useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { AuditEntry, AuditList, EventsList } from '@/lib/admin/types'
import { formatStamp } from '@/lib/admin/zoned'
import { InputField, SelectField } from '../form'
import { EmptyState, LoadError, PageHeader, Pagination, SkeletonBlock, Spinner } from '../primitives'

const PAGE_SIZE = 50
const ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

type Names = Map<string, string>

export function AuditPage() {
  const { me } = useAdminSession()
  const tz = me.programme.timezone
  const [preset, setPreset] = useState('')
  const [custom, setCustom] = useState('')
  const [action, setAction] = useState('')
  const [page, setPage] = useState(1)
  const audit = useAdminQuery<AuditList>('/admin/audit', { page, page_size: PAGE_SIZE, action })
  const events = useAdminQuery<EventsList>('/admin/events', { include_archived: true })

  // A typed prefix wins over the preset; apply it once typing pauses.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setAction(custom.trim() || preset)
      setPage(1)
    }, 300)
    return () => window.clearTimeout(id)
  }, [custom, preset])

  useAdminSignal((signal) => {
    if (page === 1 && (signal.type === 'state' || signal.type === 'email' || signal.type === 'resync')) audit.reloadSoon(3000)
  })

  const names: Names = new Map((events.data?.events ?? []).map((event) => [event.id, event.name]))
  const data = audit.data

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every change by organisers, the schedule and the system, plus participant sign-ins. Newest first."
      />
      <section aria-labelledby="audit-title" className="mt-6 rounded-[var(--radius-card)] border border-sand bg-paper">
        <div className="space-y-4 px-4 pt-4 sm:px-6 sm:pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="audit-title" className="text-[1.05rem] font-bold text-plum">
              Activity
            </h2>
            <p className="text-sm text-plum-soft" aria-live="polite">
              {audit.pending && audit.stale ? <Spinner label="Updating" /> : data ? plural(data.total, 'entry', 'entries') : null}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:max-w-3xl">
            <SelectField label="Show" value={preset} onChange={(e) => setPreset(e.target.value)} disabled={custom.trim() !== ''}>
              {AUDIT_PREFIXES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </SelectField>
            <InputField
              label="Action code starts with"
              optional
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              placeholder="For example event.opened"
              autoCapitalize="none"
              spellCheck={false}
              hint={custom.trim() ? 'Overrides the list on the left.' : undefined}
            />
          </div>
        </div>
        <div className="px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
          {!data ? (
            audit.error ? (
              <LoadError error={audit.error} onRetry={() => void audit.reload()} what="the audit log" />
            ) : (
              <div className="space-y-2" aria-busy="true">
                {Array.from({ length: 6 }, (_, i) => (
                  <SkeletonBlock key={i} className="h-16 w-full" />
                ))}
              </div>
            )
          ) : data.items.length === 0 ? (
            <EmptyState icon={<ScrollText aria-hidden className="size-6" />} title="No matching activity">
              {action ? 'Nothing starts with that action code. Try a shorter prefix such as event.' : 'Nothing has been recorded yet.'}
            </EmptyState>
          ) : (
            <div className={audit.stale ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
              <ol className="divide-y divide-sand">
                {data.items.map((entry) => (
                  <AuditRow key={entry.id} entry={entry} timeZone={tz} names={names} />
                ))}
              </ol>
              <Pagination
                page={data.page}
                pageSize={PAGE_SIZE}
                total={data.total}
                label="Audit log pages"
                busy={audit.pending}
                onPage={(next) => {
                  setPage(next)
                  document.getElementById('audit-title')?.scrollIntoView({ block: 'start' })
                }}
              />
            </div>
          )}
        </div>
      </section>
    </>
  )
}

function actorText(entry: AuditEntry): { icon: React.ReactNode; text: string } {
  if (entry.actor_type === 'admin') return { icon: <ShieldCheck aria-hidden className="size-4" />, text: entry.actor ?? 'An organiser' }
  if (entry.actor_type === 'participant') {
    return { icon: <UserRound aria-hidden className="size-4" />, text: `Participant ${entry.actor ?? ''}`.trim() }
  }
  return { icon: <Bot aria-hidden className="size-4" />, text: entry.actor && entry.actor !== 'system' ? entry.actor : 'System' }
}

function AuditRow({ entry, timeZone, names }: { entry: AuditEntry; timeZone: string; names: Names }) {
  const actor = actorText(entry)
  const details = entry.details && Object.keys(entry.details).length ? entry.details : null
  const target =
    entry.target_type && entry.target_id
      ? `${humanize(entry.target_type)}: ${names.get(entry.target_id) ?? shortId(entry.target_id)}`
      : null
  return (
    <li className="py-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="font-semibold text-plum">
          {auditActionLabel(entry.action)}
          <span className="ml-2 text-xs font-normal text-plum-soft">{entry.action}</span>
        </p>
        <p className="tabular text-sm text-plum-soft">
          <time dateTime={entry.at}>{formatStamp(entry.at, timeZone)}</time>
        </p>
      </div>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-sm text-plum-soft">
        <span className="inline-flex items-center gap-1.5">
          {actor.icon}
          <span className="break-all">{actor.text}</span>
        </span>
        {target ? <span className="break-words">, {target}</span> : null}
      </p>
      {details ? <Details value={details} timeZone={timeZone} names={names} /> : null}
      {entry.request_id ? <p className="mt-1 text-xs text-plum-soft">Request {shortId(entry.request_id)}</p> : null}
    </li>
  )
}

function shortId(id: string): string {
  return id.replace(/-/g, '').slice(0, 8)
}

function formatValue(value: unknown, timeZone: string, names: Names): string {
  if (value === null || value === undefined || value === 'None' || value === '') return 'none'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') return value.toLocaleString('en-IN')
  if (typeof value === 'string') {
    if (ISO.test(value) && !Number.isNaN(Date.parse(value))) return formatStamp(value, timeZone)
    if (UUID.test(value)) return names.get(value) ?? shortId(value)
    return value
  }
  if (Array.isArray(value)) {
    const items = value.map((item) => formatValue(item, timeZone, names))
    if (items.length > 6) return `${items.slice(0, 5).join(', ')} and ${items.length - 5} more`
    return items.join(', ') || 'none'
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, inner]) => `${humanize(key)}: ${formatValue(inner, timeZone, names)}`)
      .join('; ')
  }
  return String(value)
}

function isChange(value: unknown): value is { from: unknown; to: unknown } {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && 'from' in value && 'to' in value
}

/** Audit details as plain sentences: "Name: Old → New", lists shortened, IDs named. */
function Details({ value, timeZone, names }: { value: Record<string, unknown>; timeZone: string; names: Names }) {
  const entries = Object.entries(value).filter(([, inner]) => inner !== null && inner !== undefined && inner !== '')
  if (!entries.length) return null
  return (
    <dl className="mt-2 grid gap-x-4 gap-y-1 rounded-2xl bg-ivory px-3 py-2.5 text-sm sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
      {entries.map(([key, inner]) => (
        <div key={key} className="contents">
          <dt className="text-plum-soft">{humanize(key)}</dt>
          <dd className="min-w-0 break-words text-plum">
            {isChange(inner) ? (
              <>
                {formatValue(inner.from, timeZone, names)} <span aria-hidden>→</span>
                <span className="sr-only">changed to</span> {formatValue(inner.to, timeZone, names)}
              </>
            ) : key === 'participant_ids' && Array.isArray(inner) ? (
              plural(inner.length, 'participant')
            ) : key === 'order' && Array.isArray(inner) ? (
              `${plural(inner.length, 'event')} in a new order`
            ) : (
              formatValue(inner, timeZone, names)
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
