// StageVote API stress test for k6 (https://grafana.com/docs/k6/).
//
// Run it only through tests/load/run.sh (for example `--users 500 --requests 10000`), which
// starts an isolated Compose project with synthetic accounts and refuses any other target.
//
// USERS virtual users share exactly REQUESTS API requests between them, sent as fast as the
// server answers (plus THINK_MS of pause after each, if set). Each user first opens their own
// sign-in link as the link page does (their first two requests: the link check, then the tap
// that signs in), then makes the calls a voter's phone makes, in this mix:
//
//   35%  GET  /api/v1/me/snapshot            the voting home screen
//   25%  GET  /api/v1/me/events/{id}         the live round with its ballot
//   10%  GET  /api/v1/time                   clock sync
//   10%  GET  /api/v1/public/programme       the sign-in page
//   20%  POST /api/v1/me/ballots/{id}/vote   first time: casts the user's vote
//        GET  /api/v1/me/ballots/{id}/receipt  afterwards: reads the receipt back
//
// Setup (not counted) opens round 1 for voting; teardown closes it. Every accepted vote is
// counted, so run.sh can check that the database stored exactly as many.
import http from 'k6/http'
import exec from 'k6/execution'
import { SharedArray } from 'k6/data'
import { sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const TARGET = __ENV.TARGET
const ORIGIN = __ENV.ORIGIN
const USERS = Number(__ENV.USERS || 500)
const REQUESTS = Number(__ENV.REQUESTS || 10000)
const THINK_MS = Number(__ENV.THINK_MS || 0)
const MAX_MINUTES = Number(__ENV.MAX_MINUTES || 30)

if (!TARGET || !ORIGIN || !__ENV.ACCOUNTS_FILE) {
  throw new Error('TARGET, ORIGIN and ACCOUNTS_FILE are required (use tests/load/run.sh)')
}
if (REQUESTS < 2 * USERS) throw new Error('REQUESTS must be at least twice USERS: every user checks their link and signs in once')

// One sign-in link token per line (written by seed-loadtest). A link works on one device only,
// so every user needs a token of their own.
const tokens = new SharedArray('tokens', () =>
  open(__ENV.ACCOUNTS_FILE)
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean),
)
if (tokens.length < USERS) throw new Error(`ACCOUNTS_FILE holds ${tokens.length} sign-in links for ${USERS} users`)

export const options = {
  // Each user stays signed in across iterations, on the same "phone" (k6 would otherwise clear
  // cookies, the session and the device cookie, every time).
  noCookiesReset: true,
  setupTimeout: '120s',
  teardownTimeout: '120s',
  scenarios: {
    api: {
      executor: 'shared-iterations',
      vus: USERS,
      iterations: REQUESTS,
      maxDuration: `${MAX_MINUTES}m`,
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    'http_req_failed{scenario:api}': ['rate<0.01'],
    'http_req_duration{scenario:api}': ['p(95)<1000'],
    unexpected_errors: ['count==0'],
  },
}

// 409 answers (already voted, voting closed) are part of normal voting, not failures.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409))

const votesAccepted = new Counter('votes_accepted')
const unexpectedErrors = new Counter('unexpected_errors')
const loginLatency = new Trend('login_latency', true)

// Per virtual user (k6 keeps module state per VU).
let linkChecked = false
let signedIn = false
let csrf = ''
let voted = false
const headers = { Origin: ORIGIN, 'Content-Type': 'application/json' }

function unexpected(where, res) {
  unexpectedErrors.add(1)
  console.error(`${where}: HTTP ${res.status} ${String(res.body).slice(0, 160)}`)
}

export function setup() {
  const login = http.post(
    `${TARGET}/api/v1/admin/auth/login`,
    JSON.stringify({ email: __ENV.ADMIN_EMAIL, password: __ENV.ADMIN_PASSWORD }),
    { headers, tags: { name: 'setup' } },
  )
  if (login.status !== 200) throw new Error(`admin login failed: HTTP ${login.status}`)
  const me = http.get(`${TARGET}/api/v1/admin/me`, { headers, tags: { name: 'setup' } }).json()
  const adminHeaders = { ...headers, 'X-CSRF-Token': me.csrf_token }
  const live = http.get(`${TARGET}/api/v1/admin/live`, { headers, tags: { name: 'setup' } }).json()
  const next = live.events.find((e) => e.status === 'upcoming')
  if (!next) throw new Error('no upcoming round to open')
  const deadline = new Date(Date.now() + (MAX_MINUTES + 10) * 60 * 1000).toISOString()
  const opened = http.post(
    `${TARGET}/api/v1/admin/live/open`,
    JSON.stringify({ event_id: next.id, expected_live_version: live.live_version, deadline_at: deadline }),
    { headers: adminHeaders, tags: { name: 'setup' } },
  )
  if (opened.status !== 200) throw new Error(`could not open the round: HTTP ${opened.status} ${opened.body}`)
  return { eventId: next.id, adminCsrf: me.csrf_token }
}

export default function (data) {
  const index = exec.vu.idInTest - 1
  if (!signedIn) {
    const token = tokens[index]
    if (!linkChecked) {
      // As on the link page, the check comes before the tap. Its answer gives this user's phone
      // the device cookie, so a sign-in whose answer is lost can be retried from the same phone
      // without being refused as another device.
      const res = http.post(`${TARGET}/api/v1/auth/link/check`, JSON.stringify({ token }), {
        headers,
        tags: { name: 'POST /auth/link/check' },
      })
      if (res.status !== 200) {
        unexpected('link check', res)
        return
      }
      linkChecked = true
    } else {
      const res = http.post(`${TARGET}/api/v1/auth/link`, JSON.stringify({ token }), {
        headers,
        tags: { name: 'POST /auth/link' },
      })
      loginLatency.add(res.timings.duration)
      if (res.status !== 200) {
        unexpected('sign-in', res)
        return
      }
      signedIn = true
    }
    if (THINK_MS) sleep(THINK_MS / 1000)
    return
  }

  // The snapshot carries the CSRF token that votes need, so a user's first call after
  // signing in is always the snapshot (as on a real phone).
  const roll = csrf ? Math.random() * 100 : 0
  if (roll < 35) {
    const res = http.get(`${TARGET}/api/v1/me/snapshot`, { headers, tags: { name: 'GET /me/snapshot' } })
    if (res.status !== 200) unexpected('snapshot', res)
    else {
      try {
        csrf = res.json().csrf_token || csrf
      } catch (_) {
        /* keep the previous token */
      }
    }
  } else if (roll < 60) {
    const res = http.get(`${TARGET}/api/v1/me/events/${data.eventId}`, { headers, tags: { name: 'GET /me/events/{id}' } })
    if (res.status !== 200) unexpected('event', res)
  } else if (roll < 70) {
    const res = http.get(`${TARGET}/api/v1/time`, { headers, tags: { name: 'GET /time' } })
    if (res.status !== 200) unexpected('time', res)
  } else if (roll < 80) {
    const res = http.get(`${TARGET}/api/v1/public/programme`, { headers, tags: { name: 'GET /public/programme' } })
    if (res.status !== 200) unexpected('programme', res)
  } else {
    voteOrReceipt(data)
  }
  if (THINK_MS) sleep(THINK_MS / 1000)
}

const ballots = {} // per VU: event detail cache (ballot id and nominee ids)

function voteOrReceipt(data) {
  if (!ballots.id) {
    const res = http.get(`${TARGET}/api/v1/me/events/${data.eventId}`, { headers, tags: { name: 'GET /me/events/{id}' } })
    if (res.status !== 200) return unexpected('event', res)
    const ballot = res.json().ballots[0]
    ballots.id = ballot.id
    ballots.nominees = ballot.nominees.map((n) => n.id)
    voted = Boolean(ballot.receipt)
    return
  }
  if (voted) {
    const res = http.get(`${TARGET}/api/v1/me/ballots/${ballots.id}/receipt`, {
      headers,
      tags: { name: 'GET /me/ballots/{id}/receipt' },
    })
    if (res.status !== 200) unexpected('receipt', res)
    return
  }
  const nominee = ballots.nominees[Math.floor(Math.random() * ballots.nominees.length)]
  const res = http.post(`${TARGET}/api/v1/me/ballots/${ballots.id}/vote`, JSON.stringify({ nominee_id: nominee }), {
    headers: { ...headers, 'X-CSRF-Token': csrf, 'Idempotency-Key': `${exec.vu.idInTest}-${ballots.id}` },
    tags: { name: 'POST /me/ballots/{id}/vote' },
  })
  if (res.status === 201 || res.status === 200) {
    votesAccepted.add(1)
    voted = true
  } else if (res.status === 409) {
    voted = true // already voted (for example after a retry): not an error
  } else unexpected('vote', res)
}

export function teardown(data) {
  const live = http.get(`${TARGET}/api/v1/admin/live`, { headers, tags: { name: 'setup' } }).json()
  if (live.current_event) {
    http.post(
      `${TARGET}/api/v1/admin/live/close`,
      JSON.stringify({ event_id: live.current_event.id, expected_live_version: live.live_version }),
      { headers: { ...headers, 'X-CSRF-Token': data.adminCsrf }, tags: { name: 'setup' } },
    )
  }
}

export function handleSummary(data) {
  const m = (name, stat) => (data.metrics[name] ? data.metrics[name].values[stat] : undefined)
  const text = [
    `users=${USERS} requests=${REQUESTS} think_ms=${THINK_MS}`,
    `http_reqs=${m('http_reqs', 'count')} failed_rate=${m('http_req_failed', 'rate')}`,
    `latency ms p50=${m('http_req_duration', 'med')} p95=${m('http_req_duration', 'p(95)')} p99=${m('http_req_duration', 'p(99)')}`,
    `votes_accepted=${m('votes_accepted', 'count') || 0} unexpected_errors=${m('unexpected_errors', 'count') || 0}`,
  ].join('\n')
  return {
    '/out/summary.json': JSON.stringify(data, null, 2),
    '/out/summary.txt': text + '\n',
    stdout: text + '\n',
  }
}
