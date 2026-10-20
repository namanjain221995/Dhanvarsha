'use client'

import { Ban, CheckCircle2, Circle, Clock3, Mail, MailWarning, MailX, Monitor, MonitorOff } from 'lucide-react'
import { EMAIL_KIND_LABELS, EMAIL_STATUS_LABELS } from '@/lib/admin/labels'
import type { ParticipantRow } from '@/lib/admin/types'
import { formatWhen } from '@/lib/admin/zoned'
import { StatusChip, type Tone } from '../primitives'

type Status = { tone: Tone; label: string; icon: React.ReactNode; detail: string | null }

/** Invitation and first sign-in state, in words, from a participant row. */
export function participantStatus(p: ParticipantRow, timeZone: string): Status {
  const email = p.last_email
  const emailNote =
    email && email.kind !== 'invitation'
      ? `Last email: ${EMAIL_KIND_LABELS[email.kind].toLowerCase()}, ${EMAIL_STATUS_LABELS[email.status].toLowerCase()}`
      : null
  if (p.disabled) {
    return { tone: 'danger', label: 'Disabled', icon: <Ban aria-hidden className="size-3.5" />, detail: null }
  }
  if (p.activated) {
    return {
      tone: 'success',
      label: 'Has signed in',
      icon: <CheckCircle2 aria-hidden className="size-3.5" />,
      detail: emailNote ?? (p.activated_at ? `First sign-in ${formatWhen(p.activated_at, timeZone)}` : null),
    }
  }
  if (p.invited_at) {
    const invited = `Invited ${formatWhen(p.invited_at, timeZone)}`
    if (email?.kind === 'invitation') {
      if (email.status === 'failed') {
        return { tone: 'danger', label: 'Invitation failed', icon: <MailX aria-hidden className="size-3.5" />, detail: invited }
      }
      if (email.status === 'unknown') {
        return {
          tone: 'warning',
          label: 'Invitation unconfirmed',
          icon: <MailWarning aria-hidden className="size-3.5" />,
          detail: `${invited}. The provider did not confirm it.`,
        }
      }
      if (email.status === 'queued' || email.status === 'processing') {
        return { tone: 'info', label: 'Invitation queued', icon: <Clock3 aria-hidden className="size-3.5" />, detail: invited }
      }
    }
    return { tone: 'info', label: 'Invited', icon: <Mail aria-hidden className="size-3.5" />, detail: emailNote ?? invited }
  }
  return { tone: 'neutral', label: 'Not invited', icon: <Circle aria-hidden className="size-3.5" />, detail: null }
}

export function ParticipantStatusChip({ participant, timeZone }: { participant: ParticipantRow; timeZone: string }) {
  const status = participantStatus(participant, timeZone)
  return <StatusChip tone={status.tone} icon={status.icon} label={status.label} />
}

export function SessionChip({ participant, timeZone }: { participant: ParticipantRow; timeZone: string }) {
  return participant.signed_in ? (
    <StatusChip
      tone="success"
      icon={<Monitor aria-hidden className="size-3.5" />}
      label="Signed in"
      title={participant.session_started_at ? `Since ${formatWhen(participant.session_started_at, timeZone)}` : undefined}
    />
  ) : (
    <StatusChip tone="neutral" icon={<MonitorOff aria-hidden className="size-3.5" />} label="Not signed in" />
  )
}

/** A participant can be deleted only before any invitation, email, sign-in or vote. */
export function canDelete(p: ParticipantRow): boolean {
  return !p.invited_at && !p.last_email && !p.activated && !p.signed_in && p.ballots_voted === 0
}
