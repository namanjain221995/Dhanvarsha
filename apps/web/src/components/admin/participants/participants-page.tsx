'use client'

import { FileUp, Search, Send, Settings2, UsersRound } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { describeError } from '@/lib/admin/errors'
import { plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { ImportCommit, ParticipantFilter, ParticipantList, ParticipantRow } from '@/lib/admin/types'
import { ConfirmDialog } from '../confirm'
import { controlClass, SelectField } from '../form'
import {
  EmptyState,
  FilterChips,
  FlashRegion,
  LoadError,
  MiniButton,
  PageHeader,
  Pagination,
  SkeletonBlock,
  Spinner,
  useDialog,
  useFlash,
} from '../primitives'
import { ImportPanel } from './import-panel'
import { ParticipantDialog } from './participant-dialog'
import { participantStatus, ParticipantStatusChip, SessionChip } from './status'

const PAGE_SIZE = 50

type ParticipantsDialog = { kind: 'manage'; row: ParticipantRow } | { kind: 'invite-all' }

export function ParticipantsPage() {
  const { me } = useAdminSession()
  const tz = me.programme.timezone
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<ParticipantFilter>('all')
  const [page, setPage] = useState(1)
  const [importOpen, setImportOpen] = useState<boolean | null>(null)
  const list = useAdminQuery<ParticipantList>('/admin/participants', { q, status, page, page_size: PAGE_SIZE })
  const flash = useFlash()
  const dialog = useDialog<ParticipantsDialog>()
  const [inviteBusy, setInviteBusy] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)

  // Search as you type, once typing pauses.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setQ(search.trim())
      setPage(1)
    }, 300)
    return () => window.clearTimeout(id)
  }, [search])

  useAdminSignal((signal) => {
    if (signal.type === 'email') list.reloadSoon(2000)
    else if (signal.type === 'tally') list.reloadSoon(5000)
    else if (signal.type !== 'revoked') void list.reload()
  })

  const data = list.data
  const counts = data?.counts
  const invitedCount = counts ? Math.max(0, counts.all - counts.not_invited - counts.activated - counts.disabled) : null
  const showImport = importOpen ?? (data ? data.counts.all === 0 : false)
  const filtered = q !== '' || status !== 'all'

  const filterOptions: { value: ParticipantFilter; label: string; count: number | null }[] = [
    { value: 'all', label: 'Everyone', count: counts?.all ?? null },
    { value: 'not_invited', label: 'Not invited', count: counts?.not_invited ?? null },
    { value: 'invited', label: 'Invited', count: invitedCount },
    { value: 'activated', label: 'Has signed in', count: counts?.activated ?? null },
    { value: 'signed_in', label: 'Signed in now', count: status === 'signed_in' && data && !list.stale ? data.total : null },
    { value: 'invite_failed', label: 'Email problems', count: status === 'invite_failed' && data && !list.stale ? data.total : null },
    { value: 'disabled', label: 'Disabled', count: counts?.disabled ?? null },
  ]

  const changeStatus = (value: ParticipantFilter) => {
    setStatus(value)
    setPage(1)
  }

  const inviteAll = async () => {
    setInviteBusy(true)
    setInviteError(null)
    try {
      const result = await adminAction<{ ok: boolean; queued: number }>('/admin/invitations/send', { body: { scope: 'not_invited' } })
      dialog.close()
      flash.show({
        tone: 'success',
        message: (
          <>
            {result.queued ? `${plural(result.queued, 'invitation')} queued.` : 'Everyone has already been invited.'}{' '}
            <Link href="/admin/email" className="font-semibold text-primary underline underline-offset-2">
              Track delivery
            </Link>
          </>
        ),
      })
      void list.reload()
    } catch (error) {
      setInviteError(describeError(error))
    } finally {
      setInviteBusy(false)
    }
  }

  const onImported = (result: ImportCommit, invited: boolean) => {
    const skipped = result.summary.already_imported + result.summary.invalid + result.summary.duplicate_in_input
    flash.show({
      tone: 'success',
      title: result.created ? `Imported ${plural(result.created, 'participant')}` : 'Nothing new was imported',
      message: [
        invited ? (result.invited ? `${plural(result.invited, 'invitation')} queued.` : 'No invitations were queued.') : 'Invite them when you are ready.',
        skipped ? `${plural(skipped, 'row')} skipped (already imported, invalid or repeated).` : '',
      ]
        .filter(Boolean)
        .join(' '),
    })
    setImportOpen(false)
    void list.reload()
  }

  const manageRow =
    dialog.value?.kind === 'manage'
      ? (data?.items.find((item) => item.id === (dialog.value as { row: ParticipantRow }).row.id) ?? dialog.value.row)
      : null

  return (
    <>
      <PageHeader
        title="Participants"
        description="Invite people, see who has signed in, and help anyone who is stuck."
        actions={
          <>
            <Button variant="secondary" aria-expanded={showImport} onClick={() => setImportOpen(!showImport)}>
              <FileUp aria-hidden className="size-4" />
              Add people
            </Button>
            <Button
              disabled={!counts || counts.not_invited === 0}
              onClick={(e) => {
                setInviteError(null)
                dialog.open({ kind: 'invite-all' }, e.currentTarget)
              }}
            >
              <Send aria-hidden className="size-4" />
              Invite everyone not yet invited{counts ? ` (${counts.not_invited.toLocaleString('en-IN')})` : ''}…
            </Button>
          </>
        }
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />

      <div className="mt-6 space-y-5">
        {showImport ? <ImportPanel onClose={() => setImportOpen(false)} onImported={onImported} /> : null}

        <section aria-labelledby="people-title" className="rounded-[var(--radius-card)] border border-sand bg-paper">
          <div className="space-y-4 px-4 pt-4 sm:px-6 sm:pt-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="people-title" className="text-[1.05rem] font-bold text-plum">
                People
              </h2>
              <p className="text-sm text-plum-soft" aria-live="polite">
                {list.pending && list.stale ? (
                  <Spinner label="Updating" />
                ) : data ? (
                  filtered ? `${plural(data.total, 'match', 'matches')}` : plural(data.total, 'participant')
                ) : null}
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] md:items-start">
              <div>
                <label htmlFor="participant-search" className="mb-1.5 block text-sm font-semibold text-plum">
                  Search
                </label>
                <div className="relative">
                  <Search aria-hidden className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-plum-soft" />
                  <input
                    id="participant-search"
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Name, email or user ID"
                    autoComplete="off"
                    spellCheck={false}
                    className={`${controlClass} min-h-12 border-plum-faint/70 pl-11`}
                  />
                </div>
              </div>
              <div className="sm:hidden">
                <SelectField label="Show" value={status} onChange={(e) => changeStatus(e.target.value as ParticipantFilter)}>
                  {filterOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                      {option.count !== null ? ` (${option.count})` : ''}
                    </option>
                  ))}
                </SelectField>
              </div>
              <div className="hidden sm:block md:pt-7">
                <FilterChips<ParticipantFilter> label="Show" value={status} onChange={changeStatus} options={filterOptions} />
              </div>
            </div>
          </div>

          <div className="px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
            {!data ? (
              list.error ? (
                <LoadError error={list.error} onRetry={() => void list.reload()} what="participants" />
              ) : (
                <div className="space-y-2" aria-busy="true">
                  {Array.from({ length: 6 }, (_, i) => (
                    <SkeletonBlock key={i} className="h-14 w-full" />
                  ))}
                </div>
              )
            ) : data.items.length === 0 ? (
              filtered ? (
                <EmptyState
                  title="Nobody matches"
                  action={
                    <MiniButton
                      onClick={() => {
                        setSearch('')
                        setQ('')
                        changeStatus('all')
                      }}
                    >
                      Clear search and filter
                    </MiniButton>
                  }
                >
                  Try another name, email address or user ID, or a different filter.
                </EmptyState>
              ) : (
                <EmptyState icon={<UsersRound aria-hidden className="size-6" />} title="No participants yet">
                  Import a list of email addresses to get started. Nobody receives an email until you invite them.
                </EmptyState>
              )
            ) : (
              <div className={list.stale ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
                <ParticipantTable
                  rows={data.items}
                  openedBallots={data.opened_ballots}
                  timeZone={tz}
                  onManage={(row, from) => dialog.open({ kind: 'manage', row }, from)}
                />
                <ParticipantCards
                  rows={data.items}
                  openedBallots={data.opened_ballots}
                  timeZone={tz}
                  onManage={(row, from) => dialog.open({ kind: 'manage', row }, from)}
                />
                <Pagination
                  page={data.page}
                  pageSize={data.page_size}
                  total={data.total}
                  label="Participant pages"
                  busy={list.pending}
                  onPage={(next) => {
                    setPage(next)
                    document.getElementById('people-title')?.scrollIntoView({ block: 'start' })
                  }}
                />
              </div>
            )}
          </div>
        </section>
      </div>

      {dialog.value?.kind === 'invite-all' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          title="Invite everyone not yet invited?"
          description={`${plural(counts?.not_invited ?? 0, 'participant')} will get an email with their own sign-in link. Disabled accounts are skipped.`}
          confirmLabel={`Send ${plural(counts?.not_invited ?? 0, 'invitation')}`}
          busy={inviteBusy}
          error={inviteError}
          onConfirm={() => void inviteAll()}
          onClose={dialog.close}
        />
      ) : null}
      {manageRow && data ? (
        <ParticipantDialog
          key={dialog.key}
          row={manageRow}
          openedBallots={data.opened_ballots}
          timeZone={tz}
          onClose={dialog.close}
          onChanged={() => void list.reload()}
          onDeleted={(name) => {
            dialog.close()
            flash.show({ tone: 'success', message: `${name} was removed.` })
            void list.reload()
          }}
        />
      ) : null}
    </>
  )
}

function ballotsText(row: ParticipantRow, openedBallots: number) {
  return openedBallots ? `${row.ballots_voted} of ${openedBallots}` : row.ballots_voted ? String(row.ballots_voted) : 'None open yet'
}

function ParticipantTable({
  rows,
  openedBallots,
  timeZone,
  onManage,
}: {
  rows: ParticipantRow[]
  openedBallots: number
  timeZone: string
  onManage: (row: ParticipantRow, from: Element) => void
}) {
  return (
    <div className="hidden lg:block">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Participants. Ballots voted shows completion only, never choices.</caption>
        <thead className="border-b border-sand text-plum-soft">
          <tr>
            <th scope="col" className="py-2.5 pr-4 font-semibold">
              Participant
            </th>
            <th scope="col" className="py-2.5 pr-4 font-semibold">
              User ID
            </th>
            <th scope="col" className="py-2.5 pr-4 font-semibold">
              Invitation
            </th>
            <th scope="col" className="py-2.5 pr-4 font-semibold">
              Ballots voted
            </th>
            <th scope="col" className="py-2.5 pr-4 font-semibold">
              Session
            </th>
            <th scope="col" className="py-2.5 font-semibold">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-sand">
          {rows.map((row) => {
            const status = participantStatus(row, timeZone)
            return (
              <tr key={row.id} className="align-top">
                <th scope="row" className="max-w-[18rem] py-3 pr-4 font-normal">
                  <span className="block break-words font-semibold text-plum">{row.display_name || 'No name'}</span>
                  <span className="block break-all text-plum-soft">{row.email}</span>
                </th>
                <td className="tabular whitespace-nowrap py-3 pr-4 font-semibold text-plum">{row.user_code}</td>
                <td className="py-3 pr-4">
                  <ParticipantStatusChip participant={row} timeZone={timeZone} />
                  {status.detail ? <p className="mt-1 text-plum-soft">{status.detail}</p> : null}
                </td>
                <td className="tabular whitespace-nowrap py-3 pr-4 text-plum">{ballotsText(row, openedBallots)}</td>
                <td className="py-3 pr-4">
                  <SessionChip participant={row} timeZone={timeZone} />
                </td>
                <td className="py-2 text-right">
                  <MiniButton aria-label={`Manage ${row.display_name || row.email}`} onClick={(e) => onManage(row, e.currentTarget)}>
                    <Settings2 aria-hidden className="size-4" />
                    Manage
                  </MiniButton>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ParticipantCards({
  rows,
  openedBallots,
  timeZone,
  onManage,
}: {
  rows: ParticipantRow[]
  openedBallots: number
  timeZone: string
  onManage: (row: ParticipantRow, from: Element) => void
}) {
  return (
    <ul className="space-y-3 lg:hidden">
      {rows.map((row) => {
        const status = participantStatus(row, timeZone)
        return (
          <li key={row.id} className="rounded-2xl border border-sand bg-paper p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="break-words font-semibold text-plum">{row.display_name || 'No name'}</p>
                <p className="break-all text-sm text-plum-soft">{row.email}</p>
              </div>
              <MiniButton aria-label={`Manage ${row.display_name || row.email}`} onClick={(e) => onManage(row, e.currentTarget)}>
                Manage
              </MiniButton>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <div className="min-w-0">
                <dt className="text-plum-soft">User ID</dt>
                <dd className="tabular font-semibold text-plum">{row.user_code}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-plum-soft">Ballots voted</dt>
                <dd className="tabular font-semibold text-plum">{ballotsText(row, openedBallots)}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-plum-soft">Invitation</dt>
                <dd className="mt-1 min-w-0">
                  <ParticipantStatusChip participant={row} timeZone={timeZone} />
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-plum-soft">Session</dt>
                <dd className="mt-1 min-w-0">
                  <SessionChip participant={row} timeZone={timeZone} />
                </dd>
              </div>
            </dl>
            {status.detail ? <p className="mt-2 text-sm text-plum-soft">{status.detail}</p> : null}
          </li>
        )
      })}
    </ul>
  )
}
