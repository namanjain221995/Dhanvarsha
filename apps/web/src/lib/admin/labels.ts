import type { EmailJobKind, EmailJobStatus, ScheduleJobStatus } from './types'

export const CLOSE_REASONS: Record<string, string> = {
  manual: 'Closed by an organiser',
  deadline: 'Closed at its deadline',
  superseded: 'Closed when the next event started',
  superseded_by_schedule: 'Closed when the schedule opened the next event',
  programme_ended: 'Closed when the programme ended',
  restarted: 'Closed when the programme restarted from round 1',
}

export const SCHEDULE_JOB_LABELS: Record<ScheduleJobStatus, string> = {
  pending: 'Waiting',
  done: 'Opened',
  skipped: 'Skipped',
  missed: 'Missed',
  stale: 'Replaced',
  failed: 'Failed',
}

export const EMAIL_STATUS_LABELS: Record<EmailJobStatus, string> = {
  queued: 'Queued',
  processing: 'Sending',
  accepted: 'Accepted',
  failed: 'Failed',
  unknown: 'Unknown',
  superseded: 'Replaced',
  cancelled: 'Cancelled',
}

export const EMAIL_STATUS_HELP: Record<EmailJobStatus, string> = {
  queued: 'Waiting for the email worker.',
  processing: 'Being handed to the provider now.',
  accepted: 'The provider accepted it. This does not prove it reached the inbox.',
  failed: 'Not sent. Retry, or send a new invitation if the credential expired.',
  unknown: 'The provider may or may not have accepted it. A retry can send a second copy.',
  superseded: 'A newer email with a fresh credential replaced this one.',
  cancelled: 'Stopped before sending.',
}

export const EMAIL_KIND_LABELS: Record<EmailJobKind, string> = {
  invitation: 'Invitation',
  login_reminder: 'Login reminder (retired)',
  password_reset: 'Reset link (retired)',
  test: 'Invitation preview',
}

const AUDIT_ACTIONS: Record<string, string> = {
  'admin.login': 'Organiser signed in',
  'asset.uploaded': 'Image uploaded',
  'ballot.corrected': 'Ballot corrected after opening',
  'ballot.created': 'Ballot added',
  'ballot.deleted': 'Ballot removed',
  'ballot.updated': 'Ballot edited',
  'email.diagnosed': 'Email diagnostics run',
  'email.retried': 'Email retried',
  'email.retried_failed': 'Failed emails retried',
  'email.template_reset': 'Invitation wording reset to built-in',
  'email.template_saved': 'Invitation wording saved',
  'email.test_queued': 'Invitation preview emailed',
  'event.archived': 'Event archived',
  'event.closed': 'Voting closed',
  'event.created': 'Event created',
  'event.deadline_changed': 'Deadline changed',
  'event.opened': 'Voting opened',
  'event.presentation_corrected': 'Event details corrected after opening',
  'event.reordered': 'Events reordered',
  'event.started_next': 'Next event started',
  'event.unarchived': 'Event restored',
  'event.updated': 'Event edited',
  'invitation.issued': 'Invitations issued',
  'invitation.login_reminder': 'Login reminder queued',
  'nominee.corrected': 'Nominee corrected after opening',
  'nominee.created': 'Nominee added',
  'nominee.deleted': 'Nominee removed',
  'nominee.reordered': 'Nominees reordered',
  'nominee.updated': 'Nominee edited',
  'participant.activated': 'Participant activated their account (retired flow)',
  'participant.deleted': 'Participant removed',
  'participant.disabled': 'Participant disabled',
  'participant.enabled': 'Participant enabled',
  'participant.login': 'Participant signed in',
  'participant.password_reset': 'Participant reset their password',
  'participant.renamed': 'Participant renamed',
  'participant.session_reset': 'Participant session reset',
  'participants.imported': 'Participants imported',
  'programme.ended': 'Programme ended',
  'programme.restarted': 'Programme restarted from round 1',
  'programme.reopened': 'Programme resumed',
  'programme.updated': 'Programme settings changed',
  'recovery.issued': 'Reset link queued',
  'results.exported': 'Results exported',
  'results.published': 'Results published',
  'results.unpublished': 'Results unpublished',
  'schedule.failed': 'Scheduled opening failed',
  'schedule.missed': 'Scheduled opening missed',
  'schedule.resumed': 'Schedule resumed',
}

export function auditActionLabel(action: string): string {
  return AUDIT_ACTIONS[action] ?? humanize(action.replace(/\./g, ' '))
}

/** Filter presets for the audit log (prefix match on the action code). */
export const AUDIT_PREFIXES: { value: string; label: string }[] = [
  { value: '', label: 'All activity' },
  { value: 'event.', label: 'Events and live control' },
  { value: 'schedule.', label: 'Schedule' },
  { value: 'results.', label: 'Results' },
  { value: 'programme.', label: 'Programme' },
  { value: 'ballot.', label: 'Ballots' },
  { value: 'nominee.', label: 'Nominees' },
  { value: 'participant', label: 'Participants' },
  { value: 'invitation.', label: 'Invitations' },
  { value: 'email.', label: 'Email' },
  { value: 'admin.', label: 'Organiser sign-ins' },
  { value: 'asset.', label: 'Uploads' },
]

export function humanize(code: string): string {
  const text = code.replace(/_/g, ' ').trim()
  return text ? text[0]!.toUpperCase() + text.slice(1) : code
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString('en-IN')} ${count === 1 ? one : many}`
}
