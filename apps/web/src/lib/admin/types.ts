/**
 * Response shapes of the admin API (`/api/v1/admin/*`). The FastAPI handlers return plain
 * dicts, so these mirror `apps/api/src/stagevote/api/admin_*.py` field for field.
 * Timestamps are ISO 8601 strings in UTC.
 */

export type AdminMe = {
  admin: { id: string; email: string; display_name: string }
  csrf_token: string
  programme: { id: string; name: string; timezone: string }
  environment: string
  product_name: string
}

export type Accent = 'violet' | 'plum' | 'indigo' | 'teal' | 'rose' | 'amber' | 'gold'
export type RunMode = 'manual' | 'scheduled'
export type LoginPhase = 'before_open' | 'open' | 'closed'
export type EventStatus = 'upcoming' | 'open' | 'closed'
export type EffectiveStatus = EventStatus | 'closing'
/** team / individual: votes for nominees. mcq: multiple-choice question. text: typed-answer question. */
export type BallotKind = 'team' | 'individual' | 'mcq' | 'text'
/** POST /admin/events: the first ballot's kind, or "none" for a round without voting. */
export type NewEventBallotKind = BallotKind | 'none'

// ---- live control -------------------------------------------------------------

export type NomineeResult = {
  nominee_id: string
  name: string
  affiliation: string
  votes: number
  percent: number
  leading: boolean
}

/** One typed answer, as organisers see it on the results page (never shown to participants). */
export type TypedAnswer = {
  participant_name: string
  participant_email: string
  answer: string
  created_at: string
}

/**
 * Votes and multiple-choice answers per option. Typed-answer questions (kind "text") carry
 * `answers` and `answer_count` instead; their vote fields may be missing or zero.
 */
export type BallotResult = {
  ballot_id: string
  title: string
  kind: BallotKind
  nominees: NomineeResult[]
  total_votes: number
  eligible_voters: number
  remaining_voters: number
  leaders: string[]
  tie: boolean
  answers?: TypedAnswer[]
  answer_count?: number
}

export type LiveEvent = {
  id: string
  round: number
  name: string
  status: EventStatus
  effective_status: EffectiveStatus
  planned_start_at: string | null
  planned_end_at: string | null
  opened_at: string | null
  voting_deadline_at: string | null
  closed_at: string | null
  close_reason: string | null
  opened_by: 'manual' | 'schedule' | null
  results_published: boolean
  voters: number
}

export type ScheduleJobStatus = 'pending' | 'done' | 'skipped' | 'missed' | 'stale' | 'failed'

export type ScheduleJob = {
  id: string
  event_id: string
  event_name: string
  action: string
  run_at: string
  window_end_at: string
  revision: number
  status: ScheduleJobStatus
  detail: string | null
  executed_at: string | null
}

export type LiveState = {
  server_time: string
  state_version: number
  live_version: number
  programme: {
    name: string
    timezone: string
    run_mode: RunMode
    schedule_suspended: boolean
    schedule_suspended_at: string | null
    schedule_revision: number
    login_opens_at: string | null
    login_closes_at: string | null
    ended_at: string | null
  }
  turnout: { eligible: number; signed_in: number }
  current_event: (LiveEvent & { results: BallotResult[] | null }) | null
  next_event: LiveEvent | null
  events: LiveEvent[]
  schedule_jobs: ScheduleJob[]
  worker: { id: string | null; last_seen_at: string | null; healthy: boolean }
  live_channel: { listener_connected: boolean; connections_here: number }
}

export type SchedulePlanOutcome =
  | 'opens_now'
  | 'opens_later'
  | 'window_passed'
  | 'closes_when_next_opens'
  | 'stays_open_until_deadline'

export type SchedulePreview = {
  server_time: string
  live_version: number
  plan: {
    event_id: string
    name: string
    outcome: SchedulePlanOutcome
    planned_start_at: string | null
    planned_end_at: string | null
  }[]
}

// ---- programme ----------------------------------------------------------------

export type ProgrammeSettings = {
  id: string
  slug: string
  name: string
  description: string
  instructions: string
  help_contact: string
  logo_asset_id: string | null
  logo_url: string | null
  banner_asset_id: string | null
  banner_url: string | null
  accent: Accent
  accents: Accent[]
  timezone: string
  login_opens_at: string | null
  login_closes_at: string | null
  login_phase: LoginPhase
  countdown_enabled: boolean
  run_mode: RunMode
  schedule_suspended: boolean
  schedule_suspended_at: string | null
  schedule_revision: number
  preview_upcoming: boolean
  publish_schedule: boolean
  ended_at: string | null
  settings_version: number
  live_version: number
  server_time: string
}

export type UploadedAsset = { id: string; url: string; width: number; height: number }

/** POST /admin/media/uploads/{id}/complete: an opening_media asset (videos have width = height = 0). */
export type UploadedMedia = AdminOpeningMedia & { id: string; width: number; height: number }
export type AssetKind = 'programme_logo' | 'banner' | 'event_logo' | 'nominee_photo'

// ---- opening page -------------------------------------------------------------

/** One card of the opening page (/opening) as the editor sees it. `id` is chosen by the client. */
export type AdminOpeningCard = {
  id: string
  title: string
  /** One of `AdminOpening.icons`. */
  icon: string
  /** An uploaded picture (kind event_logo) shown in the circle instead of the icon. */
  image_asset_id: string | null
  image_url: string | null
  /** "", an http(s) URL or a path on this site starting with "/". Used only without media. */
  link: string
  /** An uploaded picture or video (kind opening_media) the button shows full screen. */
  media_asset_id: string | null
  /** What `media_asset_id` points to (GET only; saving sends the id). */
  media?: AdminOpeningMedia | null
}

/** A card's uploaded picture or video as the editor shows it. */
export type AdminOpeningMedia = { url: string; type: 'image' | 'video'; name: string; byte_size: number }

/** The page's content: what is saved, merged over the defaults (or the defaults themselves). */
export type OpeningContent = {
  title: string
  year: string
  tagline: string
  button_label: string
  /** The cards start face down and are scratched open on the screen (the default). */
  hidden: boolean
  /** Read-only: raised by POST /admin/opening/hide-all; saving keeps the stored value. */
  reveal_epoch: number
  /** An uploaded logo (kind programme_logo); null shows the built-in TechSara mark. */
  logo_asset_id: string | null
  logo_url: string | null
  cards: AdminOpeningCard[]
}

/**
 * GET, PUT and DELETE /admin/opening, and POST /admin/opening/hide-all. `settings_version` is the
 * programme's (optimistic concurrency); hiding the cards again leaves it unchanged.
 */
export type AdminOpening = {
  opening: OpeningContent
  defaults: OpeningContent
  icons: string[]
  settings_version: number
}

// ---- events -------------------------------------------------------------------

export type AdminNominee = {
  id: string
  name: string
  affiliation: string
  description: string
  members: string[]
  position: number
  photo_asset_id: string | null
  photo_url: string | null
}

export type AdminBallot = {
  id: string
  title: string
  kind: BallotKind
  description: string
  position: number
  frozen: boolean
  vote_count: number
  nominees: AdminNominee[]
}

export type AdminEvent = {
  id: string
  round: number
  position: number
  name: string
  description: string
  rules: string
  logo_asset_id: string | null
  logo_url: string | null
  status: EventStatus
  planned_start_at: string | null
  planned_end_at: string | null
  opened_at: string | null
  voting_deadline_at: string | null
  closed_at: string | null
  close_reason: string | null
  opened_by: 'manual' | 'schedule' | null
  frozen: boolean
  results_published: boolean
  archived: boolean
  version: number
  ballots: AdminBallot[]
  vote_count: number
}

export type EventsList = { events: AdminEvent[]; server_time: string; timezone: string; run_mode: RunMode }
export type EventDetail = { event: AdminEvent; server_time: string; timezone: string }

export type EventPreview = {
  server_time: string
  timezone: string
  event_count: number
  event: {
    id: string
    round: number | null
    status: string
    name: string
    description: string
    rules: string
    logo_url: string | null
    opened_at: string
    voting_deadline_at: string | null
  }
  ballots: {
    id: string
    title: string
    kind: BallotKind
    description: string
    nominees: { id: string; name: string; affiliation: string; description: string; members: string[]; photo_url: string | null }[]
  }[]
}

// ---- participants -------------------------------------------------------------

export type ImportRowStatus = 'new' | 'invalid' | 'duplicate_in_input' | 'already_imported'

export type ImportRow = {
  row: number
  input: string
  email: string | null
  display_name: string
  status: ImportRowStatus
  reason: string
}

export type ImportSummary = Record<ImportRowStatus, number>
export type ImportPreview = { summary: ImportSummary; rows: ImportRow[]; text?: string }
export type ImportCommit = { summary: ImportSummary; created: number; invited: number; rows: ImportRow[] }

export type ParticipantFilter =
  | 'all'
  | 'not_invited'
  | 'invited'
  | 'activated'
  | 'disabled'
  | 'signed_in'
  | 'invite_failed'

export type EmailJobStatus = 'queued' | 'processing' | 'accepted' | 'failed' | 'unknown' | 'superseded' | 'cancelled'
/** login_reminder and password_reset only appear on rows created before those emails were retired. */
export type EmailJobKind = 'invitation' | 'login_reminder' | 'password_reset' | 'test'

export type ParticipantRow = {
  id: string
  email: string
  display_name: string
  user_code: string
  activated: boolean
  activated_at: string | null
  disabled: boolean
  invited_at: string | null
  signed_in: boolean
  session_started_at: string | null
  last_email: { status: EmailJobStatus; kind: EmailJobKind; updated_at: string } | null
  ballots_voted: number
  synthetic: boolean
}

export type ParticipantList = {
  total: number
  page: number
  page_size: number
  opened_ballots: number
  counts: {
    all: number
    not_invited: number
    invited?: number
    activated: number
    disabled: number
    signed_in?: number
    invite_failed?: number
  }
  items: ParticipantRow[]
}

export type ParticipantDetail = {
  participant: {
    id: string
    email: string
    display_name: string
    user_code: string
    activated_at: string | null
    disabled_at: string | null
    invited_at: string | null
    /** When the newest invitation link was made, and when a device first used it (null until then). */
    link_issued_at: string | null
    link_bound_at: string | null
    created_at: string
  }
  email_jobs: {
    id: string
    kind: EmailJobKind
    status: EmailJobStatus
    attempts: number
    created_at: string
    accepted_at: string | null
    last_error: string | null
  }[]
  sessions: {
    id: string
    created_at: string
    last_seen_at: string | null
    expires_at: string
    revoked_at: string | null
    revoke_reason: string | null
    user_agent: string | null
  }[]
  ballots_voted: { event: string; ballot: string; at: string; receipt_code: string }[]
}

// ---- results ------------------------------------------------------------------

export type ResultLabel = 'final' | 'provisional' | 'not_started'

export type EventResults = {
  event_id: string
  round: number
  name: string
  status: EventStatus
  label: ResultLabel
  results_published: boolean
  closed_at: string | null
  ballots: BallotResult[]
}

export type ResultsResponse = { server_time: string; timezone: string; events: EventResults[] }

// ---- email --------------------------------------------------------------------

export type EmailStatus = {
  config: {
    provider: string
    mail_from: string
    environment: string
    smtp_host?: string
    smtp_port?: number
    smtp_tls_mode?: string
    smtp_username_set?: boolean
    smtp_password_set?: boolean
    google_service_account_file?: string | null
    google_workspace_sender?: string | null
    test_inbox_url?: string | null
  }
  counts: Partial<Record<EmailJobStatus, number>>
  worker: { id: string | null; last_seen_at: string | null; healthy: boolean; paused_until: string | null }
  recent_config_error: { last_error_code: string | null; last_error: string | null; updated_at: string } | null
  notes: string[]
}

export type InvitationWording = { subject: string; heading: string; intro: string; closing: string; button_label: string }
export type InvitationTemplate = {
  wording: InvitationWording
  defaults: InvitationWording
  customised: boolean
  updated_at: string | null
  placeholders: { token: string; meaning: string }[]
}
export type InvitationPreview = { subject: string; text: string; html: string }
export type SendToResult = { ok: boolean; created: number; invited: number; resent: number; disabled: string[]; recipients: string[] }

export type DiagnoseCheck = { name: string; status: 'ok' | 'warn' | 'error' | 'skipped'; detail: string }
export type DiagnoseResult = { provider: string; checks: DiagnoseCheck[]; config_problems: string[] }

export type EmailJob = {
  id: string
  kind: EmailJobKind
  recipient: string
  status: EmailJobStatus
  attempts: number
  max_attempts: number
  next_attempt_at: string | null
  provider: string | null
  provider_message_id: string | null
  accepted_at: string | null
  last_error_code: string | null
  last_error: string | null
  credential_available: boolean
  created_at: string
  updated_at: string
}

export type EmailJobList = { total: number; page: number; items: EmailJob[] }

export type EmailJobDetail = {
  id: string
  status: EmailJobStatus
  attempts: {
    attempt_no: number
    started_at: string | null
    finished_at: string | null
    outcome: string | null
    error_code: string | null
    error_detail: string | null
    provider_message_id: string | null
  }[]
}

// ---- audit --------------------------------------------------------------------

export type AuditEntry = {
  id: number
  at: string
  actor_type: string
  actor: string | null
  action: string
  target_type: string | null
  target_id: string | null
  details: Record<string, unknown> | null
  request_id: string | null
}

export type AuditList = { total: number; page: number; items: AuditEntry[] }
