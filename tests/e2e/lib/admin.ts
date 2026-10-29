import { type APIRequestContext, expect, request } from '@playwright/test'

export type BallotKind = 'team' | 'individual' | 'mcq' | 'text'

/** One ballot of a round made by `createRound`: a vote, or a question with its answer options. */
export type RoundBallot = { kind: BallotKind; title: string; options?: string[] }

export type TypedAnswer = { participant_name: string; participant_email: string; answer: string; created_at: string }

/** Organiser actions through the real admin API (same-origin headers, CSRF). */
export class Admin {
  private constructor(
    private readonly api: APIRequestContext,
    private readonly csrf: string,
  ) {}

  static async login(baseURL: string): Promise<Admin> {
    const email = process.env.ADMIN_EMAIL
    const password = process.env.ADMIN_PASSWORD
    if (!email || !password) throw new Error('ADMIN_EMAIL and ADMIN_PASSWORD are required')
    const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } })
    const res = await api.post('/api/v1/admin/auth/login', { data: { email, password } })
    expect(res.status(), await res.text()).toBe(200)
    const me = await (await api.get('/api/v1/admin/me')).json()
    return new Admin(api, me.csrf_token)
  }

  private async call<T = Record<string, unknown>>(method: 'get' | 'post' | 'patch' | 'delete', path: string, data?: unknown): Promise<T> {
    const res = await this.api[method](`/api/v1/admin${path}`, {
      data,
      headers: method === 'get' ? undefined : { 'X-CSRF-Token': this.csrf },
    })
    const text = await res.text()
    if (!res.ok()) throw new Error(`${method.toUpperCase()} ${path} -> ${res.status()} ${text}`)
    return (text ? JSON.parse(text) : {}) as T
  }

  live() {
    return this.call<{
      live_version: number
      current_event: { id: string; name: string } | null
      events: { id: string; name: string; status: string; round: number }[]
    }>('get', '/live')
  }

  async programme() {
    return this.call<Record<string, unknown> & { settings_version: number; ended_at: string | null }>('get', '/programme')
  }

  async patchProgramme(changes: Record<string, unknown>) {
    const current = await this.programme()
    return this.call('patch', '/programme', { settings_version: current.settings_version, ...changes })
  }

  /** Create a fresh event with one ballot and nominees, so tests never run out of rounds. */
  async createEvent(name: string, nominees: string[], kind: 'team' | 'individual' = 'team', ballots = 1) {
    const created = await this.call<{ event: { id: string; ballots: { id: string }[] } }>('post', '/events', {
      name,
      description: 'Created by the browser test suite.',
      rules: 'One vote per ballot. Votes are final.',
      ballot_kind: kind,
      ballot_title: ballots > 1 ? 'Best team' : 'Your vote',
    })
    const ballotIds = [created.event.ballots[0]!.id]
    for (let i = 1; i < ballots; i++) {
      const b = await this.call<{ id: string }>('post', `/events/${created.event.id}/ballots`, {
        title: 'Best member',
        kind: 'individual',
      })
      ballotIds.push(b.id)
    }
    for (const ballotId of ballotIds) {
      for (const n of nominees) await this.call('post', `/ballots/${ballotId}/nominees`, { name: n, affiliation: '' })
    }
    return created.event.id
  }

  /**
   * Create a round with any mix of ballots: votes (team, individual), multiple-choice
   * questions (mcq, with `options`) and typed-answer questions (text). No ballots at all makes
   * a round without voting.
   */
  async createRound(name: string, ballots: RoundBallot[]) {
    const [first, ...rest] = ballots
    const created = await this.call<{ event: { id: string; ballots: { id: string }[] } }>('post', '/events', {
      name,
      description: 'Created by the browser test suite.',
      rules: 'One answer per question. Answers are final.',
      ballot_kind: first ? first.kind : 'none',
      ...(first ? { ballot_title: first.title } : {}),
    })
    const ballotIds = first ? [created.event.ballots[0]!.id] : []
    for (const ballot of rest) {
      const made = await this.call<{ id: string }>('post', `/events/${created.event.id}/ballots`, {
        title: ballot.title,
        kind: ballot.kind,
      })
      ballotIds.push(made.id)
    }
    for (const [index, ballot] of ballots.entries()) {
      for (const option of ballot.options ?? []) await this.call('post', `/ballots/${ballotIds[index]}/nominees`, { name: option })
    }
    return { eventId: created.event.id, ballotIds }
  }

  /** Events open and voters sign in only inside the programme access window: keep it open for a while. */
  async ensureAccessOpen() {
    const p = (await this.programme()) as { login_phase?: string; login_closes_at?: string | null }
    const closesSoon = !p.login_closes_at || Date.parse(p.login_closes_at) < Date.now() + 2 * 3600_000
    if (p.login_phase !== 'open' || closesSoon) {
      await this.patchProgramme({
        login_opens_at: new Date(Date.now() - 3600_000).toISOString(),
        login_closes_at: new Date(Date.now() + 8 * 3600_000).toISOString(),
      })
    }
  }

  async ensureRunning() {
    const p = await this.programme()
    if (p.ended_at) {
      const live = await this.live()
      await this.call('post', '/live/reopen-programme', { expected_live_version: live.live_version })
    }
  }

  async open(eventId: string, deadlineSeconds = 600) {
    await this.ensureRunning()
    const live = await this.live()
    const deadline = new Date(Date.now() + deadlineSeconds * 1000).toISOString()
    const action = live.current_event ? 'start-next' : 'open'
    return this.call('post', `/live/${action}`, { event_id: eventId, expected_live_version: live.live_version, deadline_at: deadline })
  }

  async closeCurrent() {
    const live = await this.live()
    if (!live.current_event) return
    return this.call('post', '/live/close', { event_id: live.current_event.id, expected_live_version: live.live_version })
  }

  async endProgramme() {
    const live = await this.live()
    return this.call('post', '/live/end-programme', { expected_live_version: live.live_version })
  }

  async importParticipants(lines: string[]) {
    return this.call<{ created: number }>('post', '/participants/import/commit', { text: lines.join('\n'), send_invitations: false })
  }

  async findParticipant(email: string) {
    const list = await this.call<{ items: { id: string; email: string; user_code: string }[] }>(
      'get',
      `/participants?q=${encodeURIComponent(email)}`,
    )
    const found = list.items.find((p) => p.email === email)
    if (!found) throw new Error(`participant ${email} not found`)
    return found
  }

  /** One participant as the console's dialog shows them, including the state of their link. */
  participant(participantId: string) {
    return this.call<{
      participant: { id: string; email: string; link_issued_at: string | null; link_bound_at: string | null }
    }>('get', `/participants/${participantId}`)
  }

  invite(participantId: string) {
    return this.call('post', `/participants/${participantId}/invite`)
  }

  resetSession(participantId: string) {
    return this.call('post', `/participants/${participantId}/reset-session`)
  }

  publish(eventId: string) {
    return this.call('post', `/events/${eventId}/results/publish`)
  }

  results(eventId: string) {
    return this.call<{
      events: {
        ballots: {
          title: string
          kind: BallotKind
          total_votes: number
          nominees: { name: string; votes: number }[]
          /** Typed-answer questions only. */
          answer_count?: number
          answers?: TypedAnswer[]
        }[]
      }[]
    }>('get', `/results?event_id=${eventId}`)
  }

  async dispose() {
    await this.api.dispose()
  }
}
