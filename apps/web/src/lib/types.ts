/** Participant-facing API shapes (see apps/api/src/stagevote/services/snapshot.py). */

export type Accent = 'violet' | 'plum' | 'indigo' | 'teal' | 'rose' | 'amber' | 'gold'
export type LoginPhase = 'before_open' | 'open' | 'closed'

export type PublicProgramme = {
  id: string
  name: string
  description: string
  help_contact: string
  logo_url: string | null
  banner_url: string | null
  accent: Accent
  timezone: string
  login_opens_at: string | null
  login_closes_at: string | null
  countdown_enabled: boolean
  login_phase: LoginPhase
  server_time: string
  product_name?: string
  instructions?: string
}

export type EventStatus = 'live' | 'upcoming' | 'closed'

export type BallotSummary = { id: string; title: string; voted: boolean }

export type EventSummary = {
  id: string
  round: number
  status: EventStatus
  locked: boolean
  name: string | null
  logo_url: string | null
  planned_start_at: string | null
  opened_at: string | null
  voting_deadline_at: string | null
  closed_at: string | null
  ballots: BallotSummary[]
  ballot_count: number | null
  results_published: boolean
}

export type Receipt = {
  receipt_code: string
  ballot_id: string
  event_id: string
  nominee_id: string
  nominee_name: string
  ballot_title: string
  event_name: string
  accepted_at: string
}

export type Snapshot = {
  server_time: string
  state_version: number
  phase: 'live' | 'waiting' | 'completed' | 'access_closed'
  programme: PublicProgramme
  participant: { user_code: string; display_name: string }
  session_expires_at: string
  csrf_token: string
  event_count: number
  live_event_id: string | null
  next_event_id: string | null
  events: EventSummary[]
  receipts: Receipt[]
}

export type Nominee = {
  id: string
  name: string
  affiliation: string
  description: string
  /** Team ballots: who is in the team. The vote is for the whole team. */
  members: string[]
  photo_url: string | null
}

export type BallotDetail = {
  id: string
  title: string
  kind: 'team' | 'individual'
  description: string
  nominees: Nominee[]
  receipt: Receipt | null
}

export type NomineeResult = {
  nominee_id: string
  name: string
  affiliation: string
  votes: number
  percent: number
  leading: boolean
}

export type BallotResult = {
  ballot_id: string
  title: string
  kind: string
  nominees: NomineeResult[]
  total_votes: number
  eligible_voters: number
  remaining_voters: number
  leaders: string[]
  tie: boolean
}

export type EventDetail = {
  server_time: string
  state_version: number
  timezone: string
  event_count: number
  event: {
    id: string
    round: number
    status: EventStatus
    locked: boolean
    name: string | null
    description?: string
    rules?: string
    logo_url: string | null
    opened_at?: string | null
    voting_deadline_at?: string | null
    closed_at?: string | null
    planned_start_at?: string | null
    results_published?: boolean
  }
  ballots: BallotDetail[]
  results: BallotResult[] | null
  preview?: boolean
}
