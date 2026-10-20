'use client'

import { AlertTriangle, CheckCircle2, ChevronDown, CircleSlash, ExternalLink, Inbox, RotateCcw, Stethoscope, XCircle } from 'lucide-react'
import Link from 'next/link'
import { Fragment, useState } from 'react'
import { Notice } from '@/components/ui/misc'
import { describeError, errorCode } from '@/lib/admin/errors'
import { EMAIL_KIND_LABELS, EMAIL_STATUS_HELP, EMAIL_STATUS_LABELS, plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { DiagnoseCheck, DiagnoseResult, EmailJob, EmailJobDetail, EmailJobKind, EmailJobList, EmailJobStatus, EmailStatus } from '@/lib/admin/types'
import { formatRelative, formatStamp, formatWhen } from '@/lib/admin/zoned'
import { useServerNow } from '@/lib/time'
import { ConfirmDialog } from '../confirm'
import { CheckboxField, SelectField } from '../form'
import {
  EmptyState,
  FilterChips,
  FlashRegion,
  KeyValue,
  LoadError,
  MiniButton,
  PageHeader,
  Pagination,
  Panel,
  SkeletonBlock,
  Spinner,
  StatusChip,
  useDialog,
  useFlash,
  type Tone,
} from '../primitives'
import { InvitationTemplatePanel } from './invitation-template'

const PAGE_SIZE = 25
const STATUSES: EmailJobStatus[] = ['queued', 'processing', 'accepted', 'failed', 'unknown', 'superseded', 'cancelled']
const KINDS: EmailJobKind[] = ['invitation', 'test']
const STATUS_TONE: Record<EmailJobStatus, Tone> = {
  queued: 'neutral',
  processing: 'neutral',
  accepted: 'success',
  failed: 'danger',
  unknown: 'warning',
  superseded: 'neutral',
  cancelled: 'neutral',
}

type EmailDialog = { kind: 'retry-unknown'; job: EmailJob } | { kind: 'retry-all' }

export function EmailPage() {
  const { me } = useAdminSession()
  const tz = me.programme.timezone
  const status = useAdminQuery<EmailStatus>('/admin/email/status')
  const [statusFilter, setStatusFilter] = useState<'all' | EmailJobStatus>('all')
  const [kindFilter, setKindFilter] = useState<'all' | EmailJobKind>('all')
  const [page, setPage] = useState(1)
  const jobs = useAdminQuery<EmailJobList>('/admin/email/jobs', { status: statusFilter, kind: kindFilter, page, page_size: PAGE_SIZE })
  const flash = useFlash()
  const dialog = useDialog<EmailDialog>()
  const [busy, setBusy] = useState<string | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [duplicateOk, setDuplicateOk] = useState(false)

  useAdminSignal((signal) => {
    if (signal.type === 'email' || signal.type === 'poll' || signal.type === 'visible' || signal.type === 'resync') {
      status.reloadSoon(1500)
      jobs.reloadSoon(1500)
    }
  })

  const counts = status.data?.counts ?? {}
  const failedCount = counts.failed ?? 0

  const refresh = () => {
    void status.reload()
    void jobs.reload()
  }

  const retry = async (job: EmailJob, confirmDuplicate: boolean) => {
    setBusy(job.id)
    setDialogError(null)
    try {
      await adminAction(`/admin/email/jobs/${job.id}/retry`, { body: { confirm_possible_duplicate: confirmDuplicate } })
      dialog.close()
      flash.show({ tone: 'success', message: `${EMAIL_KIND_LABELS[job.kind]} to ${job.recipient} is queued again.` })
      refresh()
    } catch (error) {
      const code = errorCode(error)
      if (code === 'POSSIBLE_DUPLICATE' && !confirmDuplicate) {
        setDuplicateOk(false)
        dialog.open({ kind: 'retry-unknown', job })
      } else if (code === 'CREDENTIAL_GONE') {
        const message = (
          <>
            {describeError(error)}{' '}
            <Link href="/admin/participants" className="font-semibold text-primary underline underline-offset-2">
              Go to Participants
            </Link>
          </>
        )
        if (dialog.value) setDialogError(describeError(error))
        else flash.show({ tone: 'warning', title: 'This email cannot be resent', message })
      } else if (dialog.value) {
        setDialogError(describeError(error))
      } else {
        flash.fail(error, 'Retry failed')
      }
    } finally {
      setBusy(null)
    }
  }

  const retryAll = async () => {
    setBusy('all')
    setDialogError(null)
    try {
      const result = await adminAction<{ ok: boolean; requeued: number }>('/admin/email/jobs/retry-failed')
      dialog.close()
      flash.show({
        tone: 'success',
        message: result.requeued
          ? `${plural(result.requeued, 'failed email')} queued again.`
          : 'Nothing was queued. The failed emails no longer have their password copy; send the invitations again instead.',
      })
      refresh()
    } catch (error) {
      setDialogError(describeError(error))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <PageHeader
        title="Email"
        description="What the invitation says, sending invitations, and what the email provider said about each email."
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />
      <Notice tone="info" className="mt-5" title="“Accepted” is not the same as “delivered”">
        Accepted means the email provider took the message. It does not prove it reached the inbox: bounces and spam filtering are
        not reported back to StageVote. If someone cannot find their email, ask them to check spam, then send it again.
      </Notice>

      {!status.data ? (
        <div className="mt-5">
          {status.error ? <LoadError error={status.error} onRetry={() => void status.reload()} what="email status" /> : <SkeletonBlock className="h-48 w-full rounded-[var(--radius-card)]" />}
        </div>
      ) : (
        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <DeliveryPanel
            status={status.data}
            timeZone={tz}
            failedCount={failedCount}
            busy={busy === 'all'}
            onRetryAll={(from) => {
              setDialogError(null)
              dialog.open({ kind: 'retry-all' }, from)
            }}
          />
          <ProviderPanel status={status.data} />
        </div>
      )}

      <InvitationTemplatePanel
        timeZone={tz}
        flash={flash}
        onInvited={(result) => {
          const parts = [`Invitations queued for ${result.recipients.join(', ')}.`]
          if (result.created) parts.push(`${plural(result.created, 'new participant')} added.`)
          if (result.resent) parts.push(`${plural(result.resent, 'person', 'people')} invited before got a new password.`)
          if (result.disabled.length) parts.push(`Not sent to disabled accounts: ${result.disabled.join(', ')}.`)
          flash.show({ tone: result.disabled.length ? 'warning' : 'success', message: parts.join(' ') })
          refresh()
        }}
      />

      <section aria-labelledby="jobs-title" className="mt-5 rounded-[var(--radius-card)] border border-sand bg-paper">
        <div className="space-y-4 px-4 pt-4 sm:px-6 sm:pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="jobs-title" className="text-[1.05rem] font-bold text-plum">
              Emails
            </h2>
            <p className="text-sm text-plum-soft" aria-live="polite">
              {jobs.pending && jobs.stale ? <Spinner label="Updating" /> : jobs.data ? plural(jobs.data.total, 'email') : null}
            </p>
          </div>
          <div className="hidden lg:block">
            <FilterChips<'all' | EmailJobStatus>
              label="Status"
              value={statusFilter}
              onChange={(value) => {
                setStatusFilter(value)
                setPage(1)
              }}
              options={[
                { value: 'all', label: 'All statuses', count: STATUSES.reduce((sum, s) => sum + (counts[s] ?? 0), 0) },
                ...STATUSES.map((s) => ({ value: s, label: EMAIL_STATUS_LABELS[s], count: counts[s] ?? 0 })),
              ]}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:max-w-md lg:grid-cols-1">
            <div className="lg:hidden">
              <SelectField
                label="Status"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value as 'all' | EmailJobStatus)
                  setPage(1)
                }}
              >
                <option value="all">All statuses</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {EMAIL_STATUS_LABELS[s]} ({counts[s] ?? 0})
                  </option>
                ))}
              </SelectField>
            </div>
            <SelectField
              label="Type"
              value={kindFilter}
              onChange={(e) => {
                setKindFilter(e.target.value as 'all' | EmailJobKind)
                setPage(1)
              }}
            >
              <option value="all">All types</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {EMAIL_KIND_LABELS[k]}
                </option>
              ))}
            </SelectField>
          </div>
          {statusFilter !== 'all' ? <p className="text-sm text-plum-soft">{EMAIL_STATUS_HELP[statusFilter]}</p> : null}
        </div>
        <div className="px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
          {!jobs.data ? (
            jobs.error ? (
              <LoadError error={jobs.error} onRetry={() => void jobs.reload()} what="emails" />
            ) : (
              <SkeletonBlock className="h-40 w-full" />
            )
          ) : jobs.data.items.length === 0 ? (
            <EmptyState icon={<Inbox aria-hidden className="size-6" />} title={statusFilter === 'all' && kindFilter === 'all' ? 'No emails yet' : 'No emails match these filters'}>
              {statusFilter === 'all' && kindFilter === 'all'
                ? 'Invitations appear here once they are queued.'
                : 'Choose another status or type.'}
            </EmptyState>
          ) : (
            <div className={jobs.stale ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
              <JobTable
                jobs={jobs.data.items}
                timeZone={tz}
                busyId={busy}
                onRetry={(job, from) => {
                  if (job.status === 'unknown') {
                    setDialogError(null)
                    setDuplicateOk(false)
                    dialog.open({ kind: 'retry-unknown', job }, from)
                  } else void retry(job, false)
                }}
              />
              <ul className="divide-y divide-sand lg:hidden">
                {jobs.data.items.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    timeZone={tz}
                    busy={busy === job.id}
                    onRetry={(from) => {
                      if (job.status === 'unknown') {
                        setDialogError(null)
                        setDuplicateOk(false)
                        dialog.open({ kind: 'retry-unknown', job }, from)
                      } else void retry(job, false)
                    }}
                  />
                ))}
              </ul>
              <Pagination
                page={jobs.data.page}
                pageSize={PAGE_SIZE}
                total={jobs.data.total}
                label="Email pages"
                busy={jobs.pending}
                onPage={(next) => {
                  setPage(next)
                  document.getElementById('jobs-title')?.scrollIntoView({ block: 'start' })
                }}
              />
            </div>
          )}
        </div>
      </section>

      {dialog.value?.kind === 'retry-unknown' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          tone="danger"
          title="Send this email again?"
          description={`The provider may already have accepted the ${EMAIL_KIND_LABELS[dialog.value.job.kind].toLowerCase()} to ${dialog.value.job.recipient}. If it did, they will get a second copy.`}
          confirmLabel="Send again"
          confirmDisabled={!duplicateOk}
          busy={busy === dialog.value.job.id}
          error={dialogError}
          onConfirm={() => dialog.value?.kind === 'retry-unknown' && void retry(dialog.value.job, true)}
          onClose={dialog.close}
        >
          <CheckboxField
            label="I understand a duplicate email may be sent"
            checked={duplicateOk}
            onChange={setDuplicateOk}
          />
        </ConfirmDialog>
      ) : null}
      {dialog.value?.kind === 'retry-all' ? (
        <ConfirmDialog
          key={dialog.key}
          open
          title="Retry all failed emails?"
          description={`${plural(failedCount, 'failed email')} will be queued again where possible. Emails whose password copy expired or was replaced are skipped; send those people a new invitation. Emails with an unknown outcome are never retried in bulk.`}
          confirmLabel="Retry failed emails"
          busy={busy === 'all'}
          error={dialogError}
          onConfirm={() => void retryAll()}
          onClose={dialog.close}
        />
      ) : null}
    </>
  )
}

function DeliveryPanel({
  status,
  timeZone,
  failedCount,
  busy,
  onRetryAll,
}: {
  status: EmailStatus
  timeZone: string
  failedCount: number
  busy: boolean
  onRetryAll: (from: Element) => void
}) {
  const now = useServerNow(10_000)
  const worker = status.worker
  const pausedUntil = worker.paused_until && Date.parse(worker.paused_until) > now ? worker.paused_until : null
  return (
    <Panel title="Delivery">
      <div className="space-y-4">
        <dl className="grid grid-cols-2 gap-2 text-sm min-[420px]:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">
          {(['queued', 'accepted', 'failed', 'unknown', 'superseded', 'processing'] as EmailJobStatus[]).map((s) => (
            <div key={s} className="rounded-2xl bg-ivory px-3 py-2">
              <dt className="text-plum-soft">{EMAIL_STATUS_LABELS[s]}</dt>
              <dd className={`tabular text-[1.3rem] font-bold ${s === 'failed' && (status.counts[s] ?? 0) > 0 ? 'text-danger' : 'text-plum'}`}>
                {(status.counts[s] ?? 0).toLocaleString('en-IN')}
              </dd>
            </div>
          ))}
        </dl>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[0.92rem] font-semibold text-plum">Email worker</p>
            <p className="text-sm text-plum-soft">
              {worker.healthy
                ? `Running, last seen ${formatRelative(worker.last_seen_at, now)}.`
                : worker.last_seen_at
                  ? `Not responding since ${formatWhen(worker.last_seen_at, timeZone)}. Queued emails wait until it is back.`
                  : 'Not running. Queued emails wait until it starts.'}
            </p>
          </div>
          <StatusChip tone={worker.healthy ? 'success' : 'danger'} label={worker.healthy ? 'OK' : 'Problem'} />
        </div>
        {pausedUntil ? (
          <Notice tone="warning" title="Sending is paused">
            The worker paused sending after a configuration error and tries again at {formatWhen(pausedUntil, timeZone)}.
          </Notice>
        ) : null}
        {status.recent_config_error ? (
          <Notice tone="danger" title="Recent configuration error">
            {status.recent_config_error.last_error ?? status.recent_config_error.last_error_code} (
            {formatWhen(status.recent_config_error.updated_at, timeZone)}). Run diagnostics to check the settings.
          </Notice>
        ) : null}
        <MiniButton disabled={failedCount === 0} busy={busy} onClick={(e) => onRetryAll(e.currentTarget)}>
          <RotateCcw aria-hidden className="size-4" />
          Retry all failed{failedCount ? ` (${failedCount})` : ''}…
        </MiniButton>
      </div>
    </Panel>
  )
}

const CHECK_ICON: Record<DiagnoseCheck['status'], React.ReactNode> = {
  ok: <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />,
  warn: <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-gold-deep" />,
  error: <XCircle aria-hidden className="mt-0.5 size-4 shrink-0 text-danger" />,
  skipped: <CircleSlash aria-hidden className="mt-0.5 size-4 shrink-0 text-plum-soft" />,
}
const CHECK_WORD: Record<DiagnoseCheck['status'], string> = { ok: 'OK', warn: 'Warning', error: 'Error', skipped: 'Skipped' }

function ProviderPanel({ status }: { status: EmailStatus }) {
  const config = status.config
  const [diagnosis, setDiagnosis] = useState<DiagnoseResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const yesNo = (value: boolean | undefined) => (value ? 'Set' : 'Not set')
  const sender = /^(.*?)\s*<([^<>]+)>\s*$/.exec(config.mail_from)
  const items: [string, React.ReactNode][] = [
    ['Provider', config.provider],
    [
      'Sender',
      sender ? (
        <span key="sender">
          <span className="block">{sender[1]}</span>
          <span className="block break-all font-normal text-plum-soft">{sender[2]}</span>
        </span>
      ) : (
        config.mail_from
      ),
    ],
    ['Environment', config.environment],
  ]
  if (config.smtp_host !== undefined) {
    items.push(['SMTP server', `${config.smtp_host}:${config.smtp_port ?? ''}`])
    items.push(['Encryption', config.smtp_tls_mode ?? 'none'])
    items.push(['SMTP username', yesNo(config.smtp_username_set)])
    items.push(['SMTP password', yesNo(config.smtp_password_set)])
  }
  if (config.google_workspace_sender !== undefined) {
    items.push(['Google sender', config.google_workspace_sender ?? 'Not set'])
    items.push(['Service account file', config.google_service_account_file ?? 'Not set'])
  }

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      setDiagnosis(await adminAction<DiagnoseResult>('/admin/email/diagnose', { timeoutMs: 45_000 }))
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Provider" description="Settings come from the server configuration. Secrets are never shown.">
      <div className="space-y-4">
        <KeyValue items={items} />
        {config.test_inbox_url ? (
          <a
            href={config.test_inbox_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-primary hover:underline"
          >
            <Inbox aria-hidden className="size-4" />
            Open the test inbox
            <ExternalLink aria-hidden className="size-3.5" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        ) : null}
        <div>
          <MiniButton busy={busy} onClick={() => void run()}>
            <Stethoscope aria-hidden className="size-4" />
            Run diagnostics
          </MiniButton>
          <p className="mt-1 text-sm text-plum-soft">Checks the configuration and sign-in without sending anything.</p>
        </div>
        <div aria-live="polite" className="empty:hidden">
          {error ? (
            <Notice tone="danger" role="alert">
              {error}
            </Notice>
          ) : null}
          {diagnosis ? (
            <div className="space-y-2">
              <ul className="space-y-2">
                {diagnosis.checks.map((check, index) => (
                  <li key={`${check.name}-${index}`} className="flex items-start gap-2 text-sm">
                    {CHECK_ICON[check.status]}
                    <div className="min-w-0">
                      <p className="font-semibold text-plum">
                        {check.name}: <span className="font-normal">{CHECK_WORD[check.status]}</span>
                      </p>
                      {check.detail ? <p className="break-words text-plum-soft">{check.detail}</p> : null}
                    </div>
                  </li>
                ))}
              </ul>
              {diagnosis.config_problems.length ? (
                <Notice tone="danger" title="Configuration problems">
                  <ul className="list-disc pl-4">
                    {diagnosis.config_problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                </Notice>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  )
}

function jobFlags(job: EmailJob) {
  const retryable = job.status === 'failed' || job.status === 'unknown'
  const credentialGone = job.kind === 'invitation' && !job.credential_available
  return { retryable, credentialGone }
}

function JobStatus({ job, timeZone }: { job: EmailJob; timeZone: string }) {
  const { retryable, credentialGone } = jobFlags(job)
  return (
    <>
      <StatusChip tone={STATUS_TONE[job.status]} label={EMAIL_STATUS_LABELS[job.status]} />
      <p className="mt-1 text-sm text-plum-soft">
        {job.accepted_at
          ? `Accepted ${formatWhen(job.accepted_at, timeZone)}. `
          : job.status === 'queued' && job.next_attempt_at
            ? `Next try ${formatWhen(job.next_attempt_at, timeZone)}. `
            : ''}
        {`Attempt ${job.attempts} of ${job.max_attempts}.`}
      </p>
      {/* An earlier failed try no longer matters once the provider accepted the email. */}
      {job.last_error && job.status !== 'accepted' ? (
        <p className="mt-0.5 line-clamp-3 break-words text-sm text-plum">
          {job.status === 'queued' || job.status === 'processing' ? `Last try failed (${job.last_error}). Trying again automatically.` : job.last_error}
        </p>
      ) : null}
      {retryable && credentialGone ? (
        <p className="mt-0.5 text-sm text-gold-deep">The password copy for this email expired or was replaced. Send the invitation again instead.</p>
      ) : null}
    </>
  )
}

function JobActions({
  job,
  busy,
  open,
  onToggle,
  onRetry,
}: {
  job: EmailJob
  busy: boolean
  open: boolean
  onToggle: () => void
  onRetry: (from: Element) => void
}) {
  const { retryable, credentialGone } = jobFlags(job)
  return (
    <>
      {retryable && !credentialGone ? (
        <MiniButton
          busy={busy}
          aria-label={`Retry ${EMAIL_KIND_LABELS[job.kind].toLowerCase()} to ${job.recipient}${job.status === 'unknown' ? '…' : ''}`}
          onClick={(e) => onRetry(e.currentTarget)}
        >
          <RotateCcw aria-hidden className="size-4" />
          {job.status === 'unknown' ? 'Retry…' : 'Retry'}
        </MiniButton>
      ) : null}
      <MiniButton variant="quiet" aria-expanded={open} onClick={onToggle}>
        <ChevronDown aria-hidden className={`size-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        Attempts<span className="sr-only"> for {job.recipient}</span>
      </MiniButton>
    </>
  )
}

function JobTable({
  jobs,
  timeZone,
  busyId,
  onRetry,
}: {
  jobs: EmailJob[]
  timeZone: string
  busyId: string | null
  onRetry: (job: EmailJob, from: Element) => void
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  return (
    <table className="hidden w-full text-left text-sm lg:table">
      <caption className="sr-only">Emails, newest first</caption>
      <thead className="border-b border-sand text-plum-soft">
        <tr>
          <th scope="col" className="py-2.5 pr-4 font-semibold">
            Recipient
          </th>
          <th scope="col" className="py-2.5 pr-4 font-semibold">
            Type
          </th>
          <th scope="col" className="py-2.5 pr-4 font-semibold">
            Status
          </th>
          <th scope="col" className="py-2.5 pr-4 font-semibold">
            Created
          </th>
          <th scope="col" className="py-2.5 font-semibold">
            <span className="sr-only">Actions</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const expanded = open.has(job.id)
          return (
            <Fragment key={job.id}>
              <tr className="border-t border-sand align-top first:border-t-0">
                <th scope="row" className="max-w-[18rem] break-all py-3 pr-4 font-semibold text-plum">
                  {job.recipient}
                </th>
                <td className="whitespace-nowrap py-3 pr-4 text-plum">{EMAIL_KIND_LABELS[job.kind]}</td>
                <td className="max-w-[22rem] py-3 pr-4">
                  <JobStatus job={job} timeZone={timeZone} />
                </td>
                <td className="whitespace-nowrap py-3 pr-4 text-plum-soft">{formatWhen(job.created_at, timeZone)}</td>
                <td className="py-2 text-right">
                  <div className="flex flex-wrap justify-end gap-1">
                    <JobActions
                      job={job}
                      busy={busyId === job.id}
                      open={expanded}
                      onToggle={() => toggle(job.id)}
                      onRetry={(from) => onRetry(job, from)}
                    />
                  </div>
                </td>
              </tr>
              {expanded ? (
                <tr>
                  <td colSpan={5} className="pb-3">
                    <JobAttempts jobId={job.id} timeZone={timeZone} />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

function JobRow({ job, timeZone, busy, onRetry }: { job: EmailJob; timeZone: string; busy: boolean; onRetry: (from: Element) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="py-3">
      <div className="grid gap-x-4 gap-y-2 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto] md:items-start">
        <div className="min-w-0">
          <p className="break-all font-semibold text-plum">{job.recipient}</p>
          <p className="text-sm text-plum-soft">
            {EMAIL_KIND_LABELS[job.kind]}, created {formatWhen(job.created_at, timeZone)}
          </p>
        </div>
        <div className="min-w-0">
          <JobStatus job={job} timeZone={timeZone} />
        </div>
        <div className="flex flex-wrap gap-1 md:justify-end">
          <JobActions job={job} busy={busy} open={open} onToggle={() => setOpen((v) => !v)} onRetry={onRetry} />
        </div>
      </div>
      {open ? <JobAttempts jobId={job.id} timeZone={timeZone} /> : null}
    </li>
  )
}

function JobAttempts({ jobId, timeZone }: { jobId: string; timeZone: string }) {
  const detail = useAdminQuery<EmailJobDetail>(`/admin/email/jobs/${jobId}`)
  if (!detail.data) {
    return detail.error ? (
      <div className="mt-3">
        <LoadError error={detail.error} onRetry={() => void detail.reload()} what="the attempts" />
      </div>
    ) : (
      <SkeletonBlock className="mt-3 h-12 w-full" />
    )
  }
  if (!detail.data.attempts.length) return <p className="mt-3 rounded-2xl bg-ivory px-3 py-2.5 text-sm text-plum-soft">No attempts yet.</p>
  return (
    <ol className="mt-3 space-y-2 rounded-2xl bg-ivory px-3 py-3 text-sm">
      {detail.data.attempts.map((attempt) => (
        <li key={attempt.attempt_no}>
          <p className="font-semibold text-plum">
            Attempt {attempt.attempt_no}: {attempt.outcome ? attempt.outcome.replace(/_/g, ' ') : 'in progress'}
          </p>
          <p className="text-plum-soft">
            {attempt.started_at ? `Started ${formatStamp(attempt.started_at, timeZone)}` : ''}
            {attempt.finished_at ? `, finished ${formatStamp(attempt.finished_at, timeZone)}` : ''}.
            {attempt.provider_message_id ? ` Provider message ID ${attempt.provider_message_id}.` : ''}
          </p>
          {attempt.error_code || attempt.error_detail ? (
            <p className="break-words text-plum">
              {attempt.error_code ? `${attempt.error_code}: ` : ''}
              {attempt.error_detail}
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  )
}
