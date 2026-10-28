'use client'

import { Ban, LogOut, Mail, Trash2, UserCheck } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Notice } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'
import { EMAIL_KIND_LABELS, EMAIL_STATUS_LABELS, plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSignal } from '@/lib/admin/session'
import type { EmailJobStatus, ParticipantDetail, ParticipantRow } from '@/lib/admin/types'
import { formatStamp, formatWhen } from '@/lib/admin/zoned'
import { useServerNow } from '@/lib/time'
import { InputField } from '../form'
import { KeyValue, LoadError, MiniButton, SkeletonBlock, StatusChip, type Tone } from '../primitives'
import { canDelete, ParticipantStatusChip, SessionChip } from './status'

type ActionKind = 'invite' | 'reset-session' | 'disable' | 'enable' | 'delete'

function actionLabel(kind: ActionKind, invitedBefore: boolean): string {
  return kind === 'invite' && invitedBefore ? 'Resend invitation' : ACTIONS[kind].label
}

const ACTIONS: Record<
  ActionKind,
  { label: string; icon: React.ReactNode; description: string; tone: 'primary' | 'danger'; path: (id: string) => string; method?: 'DELETE' }
> = {
  invite: {
    label: 'Send invitation',
    icon: <Mail aria-hidden className="size-4" />,
    description:
      'Emails a new personal sign-in link. Their old link stops working and every device they are signed in on is signed out. Use this when someone changes phone, their link shows “already in use”, or the email was lost.',
    tone: 'primary',
    path: (id) => `/admin/participants/${id}/invite`,
  },
  'reset-session': {
    label: 'Sign out everywhere',
    icon: <LogOut aria-hidden className="size-4" />,
    description:
      'Signs them out on every device. They can come back on the same phone with the link in their invitation email. If the phone is lost or someone else has their link, use Send invitation instead.',
    tone: 'primary',
    path: (id) => `/admin/participants/${id}/reset-session`,
  },
  disable: {
    label: 'Disable account',
    icon: <Ban aria-hidden className="size-4" />,
    description:
      'They are signed out at once and cannot sign in or vote. Pending invitation emails are cancelled. Votes already cast still count.',
    tone: 'danger',
    path: (id) => `/admin/participants/${id}/disable`,
  },
  enable: {
    label: 'Enable account',
    icon: <UserCheck aria-hidden className="size-4" />,
    description: 'They can sign in again with the link in their latest invitation. Send a new invitation if they no longer have it.',
    tone: 'primary',
    path: (id) => `/admin/participants/${id}/enable`,
  },
  delete: {
    label: 'Delete participant',
    icon: <Trash2 aria-hidden className="size-4" />,
    description: 'Removes a mistaken import completely. Only possible before any invitation, sign-in or vote. This cannot be undone.',
    tone: 'danger',
    path: (id) => `/admin/participants/${id}`,
    method: 'DELETE',
  },
}

const SESSION_END: Record<string, string> = {
  logout: 'Signed out',
  admin_reset: 'Reset by an organiser',
  replaced: 'Replaced by a new sign-in on the same device',
  password_reset: 'Ended by a reset link (retired)',
  credentials_reissued: 'Ended by a new invitation',
  disabled: 'Ended when disabled',
  expired: 'Expired',
}

const EMAIL_TONE: Record<EmailJobStatus, Tone> = {
  queued: 'neutral',
  processing: 'neutral',
  accepted: 'success',
  failed: 'danger',
  unknown: 'warning',
  superseded: 'neutral',
  cancelled: 'neutral',
}

export function ParticipantDialog({
  row: initialRow,
  openedBallots,
  timeZone,
  onClose,
  onChanged,
  onDeleted,
}: {
  row: ParticipantRow
  openedBallots: number
  timeZone: string
  onClose: () => void
  onChanged: () => void
  onDeleted: (name: string) => void
}) {
  const detail = useAdminQuery<ParticipantDetail>(`/admin/participants/${initialRow.id}`)
  const [pending, setPending] = useState<ActionKind | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'success' | 'danger'; message: string } | null>(null)
  const [reason, setReason] = useState('')
  const [name, setName] = useState(initialRow.display_name)
  const [nameBusy, setNameBusy] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)

  const now = useServerNow(10_000)

  useAdminSignal((signal) => {
    if (signal.type === 'email') detail.reloadSoon(1500)
  })

  const data = detail.data
  const p = data?.participant
  // The list row can be out of date (or filtered away) after an action; the detail is authoritative.
  const current: ParticipantRow =
    data && p
      ? {
          ...initialRow,
          email: p.email,
          display_name: p.display_name,
          user_code: p.user_code,
          activated: Boolean(p.activated_at),
          activated_at: p.activated_at,
          disabled: Boolean(p.disabled_at),
          invited_at: p.invited_at,
          signed_in: data.sessions.some((s) => !s.revoked_at && Date.parse(s.expires_at) > now),
          session_started_at: data.sessions.find((s) => !s.revoked_at)?.created_at ?? null,
          last_email: data.email_jobs[0]
            ? { status: data.email_jobs[0].status, kind: data.email_jobs[0].kind, updated_at: data.email_jobs[0].created_at }
            : null,
          ballots_voted: data.ballots_voted.length,
        }
      : initialRow
  const row = current

  const label = row.display_name || row.email
  const available: ActionKind[] = []
  if (!row.disabled) available.push('invite')
  if (row.signed_in) available.push('reset-session')
  available.push(row.disabled ? 'enable' : 'disable')
  if (canDelete(row)) available.push('delete')

  const run = async (kind: ActionKind) => {
    const action = ACTIONS[kind]
    setBusy(true)
    setResult(null)
    try {
      const response = await adminAction<{ ok: boolean; sessions_revoked?: number }>(action.path(row.id), {
        method: action.method ?? 'POST',
        ...(kind === 'disable' ? { body: { reason: reason.trim() } } : {}),
      })
      setPending(null)
      setReason('')
      if (kind === 'delete') {
        onDeleted(label)
        return
      }
      const messages: Record<Exclude<ActionKind, 'delete'>, string> = {
        invite: row.invited_at
          ? `New invitation queued for ${label}. Their old link no longer works.`
          : `Invitation queued for ${label}.`,
        'reset-session': response.sessions_revoked
          ? `${label} is signed out of ${plural(response.sessions_revoked, 'session')}. They can come back on the same device with the link in their invitation email.`
          : `${label} had no active session. Nothing changed.`,
        disable: `${label} is disabled and signed out.`,
        enable: `${label} can sign in again.`,
      }
      setResult({ tone: 'success', message: messages[kind] })
      onChanged()
      void detail.reload()
    } catch (error) {
      setResult({ tone: 'danger', message: describeError(error) })
    } finally {
      setBusy(false)
    }
  }

  const saveName = async (event: React.FormEvent) => {
    event.preventDefault()
    setNameBusy(true)
    setNameError(null)
    try {
      await adminAction(`/admin/participants/${row.id}`, { method: 'PATCH', body: { display_name: name.trim() } })
      setResult({ tone: 'success', message: name.trim() ? `Display name changed to ${name.trim()}.` : 'Display name removed.' })
      onChanged()
      void detail.reload()
    } catch (error) {
      setNameError(describeError(error))
    } finally {
      setNameBusy(false)
    }
  }

  return (
    <Dialog open wide onClose={busy ? () => {} : onClose} title={label} description={row.display_name ? row.email : undefined}>
      <div className="space-y-6">
        <div className="flex flex-wrap gap-2">
          <ParticipantStatusChip participant={row} timeZone={timeZone} />
          <SessionChip participant={row} timeZone={timeZone} />
          <StatusChip tone="info" label={`Voted on ${row.ballots_voted} of ${openedBallots} ballots`} />
        </div>

        <div aria-live="polite" className="empty:hidden">
          {result ? (
            <Notice tone={result.tone} role={result.tone === 'danger' ? 'alert' : undefined}>
              {result.message}
            </Notice>
          ) : null}
        </div>

        <section aria-labelledby="pd-actions">
          <h3 id="pd-actions" className="text-sm font-semibold text-plum">
            Actions
          </h3>
          {pending ? (
            <div className="mt-2 rounded-2xl border border-sand-strong bg-ivory p-4">
              <p className="font-semibold text-plum">{actionLabel(pending, Boolean(row.invited_at))}?</p>
              <p className="mt-1 text-sm text-plum-soft">{ACTIONS[pending].description}</p>
              {pending === 'disable' ? (
                <InputField
                  className="mt-3"
                  label="Reason"
                  optional
                  maxLength={300}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  hint="Recorded in the audit log."
                />
              ) : null}
              <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row">
                <Button variant="secondary" onClick={() => setPending(null)} disabled={busy}>
                  Cancel
                </Button>
                <Button variant={ACTIONS[pending].tone === 'danger' ? 'danger' : 'primary'} busy={busy} onClick={() => void run(pending)}>
                  {actionLabel(pending, Boolean(row.invited_at))}
                </Button>
              </div>
            </div>
          ) : (
            <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {available.map((kind) => (
                <li key={kind}>
                  <MiniButton
                    variant={ACTIONS[kind].tone === 'danger' ? 'danger' : 'secondary'}
                    className={`w-full justify-start ${ACTIONS[kind].tone === 'danger' ? 'border border-danger/30' : ''}`}
                    onClick={() => {
                      setResult(null)
                      setPending(kind)
                    }}
                  >
                    {ACTIONS[kind].icon}
                    {actionLabel(kind, Boolean(row.invited_at))}…
                  </MiniButton>
                </li>
              ))}
            </ul>
          )}
          {!canDelete(row) ? (
            <p className="mt-2 text-sm text-plum-soft">
              Participants who were invited, signed in or voted cannot be deleted. Disable the account instead.
            </p>
          ) : null}
        </section>

        <section aria-labelledby="pd-account">
          <h3 id="pd-account" className="text-sm font-semibold text-plum">
            Account
          </h3>
          {!p ? (
            detail.error ? (
              <div className="mt-2">
                <LoadError error={detail.error} onRetry={() => void detail.reload()} what="this participant" />
              </div>
            ) : (
              <SkeletonBlock className="mt-2 h-28 w-full" />
            )
          ) : (
            <KeyValue
              className="mt-2"
              items={[
                ['User ID', <span key="code" className="tabular font-semibold">{p.user_code}</span>],
                ['Email', p.email],
                ['Added', formatStamp(p.created_at, timeZone)],
                ['Invited', p.invited_at ? formatStamp(p.invited_at, timeZone) : 'Not yet'],
                ['Invitation link', linkStatus(p, timeZone)],
                ['First signed in', p.activated_at ? formatStamp(p.activated_at, timeZone) : 'Not yet'],
                ...(p.disabled_at ? ([['Disabled', formatStamp(p.disabled_at, timeZone)]] as [string, string][]) : []),
              ]}
            />
          )}
          <form onSubmit={(e) => void saveName(e)} className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end">
            <InputField
              className="min-w-0 flex-1"
              label="Display name"
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              error={nameError}
            />
            <Button type="submit" variant="secondary" busy={nameBusy} disabled={name.trim() === row.display_name}>
              Save name
            </Button>
          </form>
        </section>

        <section aria-labelledby="pd-ballots">
          <h3 id="pd-ballots" className="text-sm font-semibold text-plum">
            Ballots voted
          </h3>
          <p className="text-sm text-plum-soft">Which ballots they completed, never whom they chose.</p>
          {data ? (
            data.ballots_voted.length ? (
              <ul className="mt-2 divide-y divide-sand rounded-2xl border border-sand">
                {data.ballots_voted.map((vote) => (
                  <li key={vote.receipt_code} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-3 py-2.5 text-sm">
                    <span className="min-w-0 break-words font-semibold text-plum">
                      {vote.event}: {vote.ballot}
                    </span>
                    <span className="text-plum-soft">
                      {formatWhen(vote.at, timeZone)}, receipt <span className="tabular font-semibold text-plum">{vote.receipt_code}</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-plum-soft">No votes yet.</p>
            )
          ) : null}
        </section>

        <section aria-labelledby="pd-emails">
          <h3 id="pd-emails" className="text-sm font-semibold text-plum">
            Emails
          </h3>
          {data ? (
            data.email_jobs.length ? (
              <ul className="mt-2 divide-y divide-sand rounded-2xl border border-sand">
                {data.email_jobs.map((job) => (
                  <li key={job.id} className="px-3 py-2.5 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-plum">{EMAIL_KIND_LABELS[job.kind]}</span>
                      <StatusChip tone={EMAIL_TONE[job.status]} label={EMAIL_STATUS_LABELS[job.status]} />
                    </div>
                    <p className="mt-0.5 text-plum-soft">
                      Created {formatWhen(job.created_at, timeZone)}
                      {job.accepted_at ? `. Accepted by the provider ${formatWhen(job.accepted_at, timeZone)}` : ''}. {plural(job.attempts, 'attempt')}.
                    </p>
                    {job.last_error ? <p className="mt-0.5 break-words text-plum">{job.last_error}</p> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-plum-soft">No emails sent yet.</p>
            )
          ) : null}
        </section>

        <section aria-labelledby="pd-sessions">
          <h3 id="pd-sessions" className="text-sm font-semibold text-plum">
            Recent sessions
          </h3>
          {data ? (
            data.sessions.length ? (
              <ul className="mt-2 divide-y divide-sand rounded-2xl border border-sand">
                {data.sessions.map((session) => (
                  <li key={session.id} className="px-3 py-2.5 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-plum">Signed in {formatWhen(session.created_at, timeZone)}</span>
                      {session.revoked_at ? (
                        <StatusChip
                          tone="neutral"
                          label={SESSION_END[session.revoke_reason ?? 'logout'] ?? `Ended (${session.revoke_reason})`}
                        />
                      ) : Date.parse(session.expires_at) > now ? (
                        <StatusChip tone="success" label="Active" />
                      ) : (
                        <StatusChip tone="neutral" label="Expired" />
                      )}
                    </div>
                    <p className="mt-0.5 break-words text-plum-soft">
                      {session.last_seen_at ? `Last seen ${formatWhen(session.last_seen_at, timeZone)}. ` : ''}
                      {session.user_agent ? summariseAgent(session.user_agent) : 'Unknown device'}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-plum-soft">Never signed in.</p>
            )
          ) : null}
        </section>

        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Done
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

/**
 * Whether their newest invitation link has been used. The first device that taps it keeps it,
 * until a new invitation replaces the link.
 */
function linkStatus(p: ParticipantDetail['participant'], timeZone: string): string {
  if (p.link_bound_at) return `In use on a device since ${formatStamp(p.link_bound_at, timeZone)}`
  if (p.link_issued_at) return 'Not used yet'
  // Someone invited before invitations carried a personal link has none yet.
  return p.invited_at ? 'None yet. Send the invitation again to email one.' : 'Not sent yet'
}

/** "Chrome on Android" style summary of a user-agent string. */
function summariseAgent(agent: string): string {
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /Firefox\//.test(agent)
      ? 'Firefox'
      : /Chrome\//.test(agent)
        ? 'Chrome'
        : /Safari\//.test(agent)
          ? 'Safari'
          : 'A browser'
  const os = /Android/.test(agent)
    ? 'Android'
    : /iPhone|iPad|iOS/.test(agent)
      ? 'iPhone or iPad'
      : /Windows/.test(agent)
        ? 'Windows'
        : /Mac OS X|Macintosh/.test(agent)
          ? 'Mac'
          : /Linux/.test(agent)
            ? 'Linux'
            : null
  return os ? `${browser} on ${os}` : browser
}
