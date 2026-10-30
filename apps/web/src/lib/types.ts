/** Participant-facing API shapes (see apps/api/src/stagevote/services/snapshot.py). */

export type Accent = 'violet' | 'plum' | 'indigo' | 'teal' | 'rose' | 'amber' | 'gold'
export type LoginPhase = 'before_open' | 'open' | 'closed'

export type PublicProgramme = {
  id: string
  name: string
  description: string
  help_contact: string
  /** The lobby's heading and message while no round is open; "" for the built-in text. */
  welcome_title: string
  welcome_message: string
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

/** POST /auth/link/check: what the personal link in an invitation can do in this browser. Never signs in. */
export type LinkCheck = { state: 'ready' | 'signed_in' | 'in_use' | 'invalid' | 'disabled' }

/** POST /auth/link: the tap on the button. reused_session is true when this browser was already signed in. */
export type LinkEntry = { ok: boolean; reused_session: boolean }

export type EventStatus = 'live' | 'upcoming' | 'closed'

/**
 * team / individual: a vote for one nominee. mcq: a multiple-choice question whose answer
 * options are nominees. text: a question answered by typing (no nominees).
 */
export type BallotKind = 'team' | 'individual' | 'mcq' | 'text'

/** Questions (mcq, text) are answered rather than voted on; the wording on screen follows. */
export const isQuestionKind = (kind: BallotKind | string | null | undefined): boolean => kind === 'mcq' || kind === 'text'

/** `kind` is optional: older servers sent only id, title and voted. */
export type BallotSummary = { id: string; title: string; voted: boolean; kind?: BallotKind }

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
  /** Null for a typed answer. */
  nominee_id: string | null
  nominee_name: string | null
  /** The typed text of an answer to a typed-answer question, otherwise null. */
  answer?: string | null
  ballot_kind?: BallotKind
  ballot_title: string
  event_name: string
  accepted_at: string
}

/** A receipt for an answer to a question (mcq or text) rather than a vote. */
export const isAnswerReceipt = (receipt: Receipt): boolean =>
  isQuestionKind(receipt.ballot_kind) || (receipt.answer !== undefined && receipt.answer !== null)

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
  kind: BallotKind
  description: string
  /** Empty for typed-answer questions. */
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

export type VoteBallotResult = {
  ballot_id: string
  title: string
  kind: 'team' | 'individual' | 'mcq'
  /** The ballot's description ("" when it has none), shown with the results. */
  description?: string
  nominees: NomineeResult[]
  total_votes: number
  eligible_voters: number
  remaining_voters: number
  leaders: string[]
  tie: boolean
}

/** Published results of a typed-answer question: only how many answered, never the texts. */
export type TextBallotResult = {
  ballot_id: string
  kind: 'text'
  answer_count: number
  title?: string
  description?: string
}

export type BallotResult = VoteBallotResult | TextBallotResult

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

/** One card of the opening page. */
export type OpeningCard = {
  id: string
  title: string
  /** An icon key (components/participant/opening-icons.tsx). */
  icon: string
  /** An uploaded picture shown in the circle instead of the icon. */
  image_url: string | null
  /** "" for a decorative button, otherwise an http(s) URL or a path on this site. */
  link: string
  /** An uploaded picture or video the button shows full screen; the link counts only without it. */
  media: OpeningMedia | null
}

/** A card's uploaded picture (WebP) or video (MP4 or WebM), served from /media. */
export type OpeningMedia = { url: string; type: 'image' | 'video' }

/** GET /public/opening: the showcase page for the big screen (/opening), defaults already applied. */
export type PublicOpening = {
  title: string
  year: string
  tagline: string
  button_label: string
  /** True: every card starts face down (its number only) and is scratched open on the screen. */
  hidden: boolean
  /** Raised by the organiser's "Hide all cards again": reveals saved under another epoch are void. */
  reveal_epoch: number
  /** Null shows the built-in TechSara mark. */
  logo_url: string | null
  cards: OpeningCard[]
  updated_at: string | null
}
