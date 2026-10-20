// StageVote live-voting load scenario for k6 (https://grafana.com/docs/k6/).
//
// Run it only through tests/load/run.sh, which starts an isolated Compose project with
// synthetic accounts and refuses any other target. What it does:
//
//   participants  VOTERS virtual users. Each signs in (spread over LOGIN_RAMP_SECONDS),
//                 loads its snapshot, and holds a live WebSocket like a phone would. On every
//                 "state" signal it refetches the snapshot (with the client's jitter) and,
//                 when a new round is live, votes once per ballot within BURST_SECONDS.
//                 About 10% send the same request twice at once (duplicate retry) and about
//                 5% lose the response (short client timeout), then recover by reading the
//                 receipt and, if none exists, retrying with the SAME idempotency key.
//                 Those 5% also try to change their vote with a new key (must get 409).
//   host          One organiser who opens round 1 and then runs start-next transitions
//                 while traffic is flowing.
//
// Results: k6 metrics plus summary.json, used by run.sh for database reconciliation.
import http from 'k6/http'
import exec from 'k6/execution'
import { SharedArray } from 'k6/data'
import { Counter, Trend } from 'k6/metrics'
import { WebSocket } from 'k6/websockets'
import { clearInterval, clearTimeout, setInterval, setTimeout } from 'k6/timers'

const TARGET = __ENV.TARGET // e.g. http://proxy (inside the load project's Docker network)
const ORIGIN = __ENV.ORIGIN // must equal the stack's APP_BASE_URL origin
const PASSWORD = __ENV.LOADTEST_PASSWORD
const VOTERS = Number(__ENV.VOTERS || 500)
const LOGIN_RAMP_SECONDS = Number(__ENV.LOGIN_RAMP_SECONDS || 30)
const BURST_SECONDS = Number(__ENV.BURST_SECONDS || 10)
const ROUND_SECONDS = Number(__ENV.ROUND_SECONDS || 40)
const ROUNDS = Number(__ENV.ROUNDS || 3)
const SETTLE_SECONDS = 20
const HOLD_SECONDS = LOGIN_RAMP_SECONDS + 15 + ROUNDS * ROUND_SECONDS + SETTLE_SECONDS

if (!TARGET || !ORIGIN || !PASSWORD || !__ENV.ACCOUNTS_FILE) {
  throw new Error('TARGET, ORIGIN, LOADTEST_PASSWORD and ACCOUNTS_FILE are required (use tests/load/run.sh)')
}

const accounts = new SharedArray('accounts', () =>
  open(__ENV.ACCOUNTS_FILE)
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean),
)

export const options = {
  scenarios: {
    participants: {
      executor: 'per-vu-iterations',
      vus: VOTERS,
      iterations: 1,
      maxDuration: `${HOLD_SECONDS + 60}s`,
      exec: 'participant',
    },
    host: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      maxDuration: `${HOLD_SECONDS + 60}s`,
      exec: 'host',
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    unexpected_errors: ['count==0'],
    receipt_mismatches: ['count==0'],
    vote_latency: ['p(95)<1000'],
    state_propagation: ['p(95)<2000'],
  },
}

// 409 answers (already voted, voting closed) are part of normal voting, not failures.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409))

const voteLatency = new Trend('vote_latency', true)
const statePropagation = new Trend('state_propagation', true)
const snapshotLatency = new Trend('snapshot_latency', true)
const loginLatency = new Trend('login_latency', true)
const transitionLatency = new Trend('transition_latency', true)
const ballotsConfirmed = new Counter('ballots_confirmed')
const votesCreated = new Counter('votes_created_201')
const votesReplayed = new Counter('votes_replayed_200')
const duplicatesDetected = new Counter('duplicate_retries')
const lostResponses = new Counter('lost_responses_simulated')
const recoveredByReceipt = new Counter('recovered_by_receipt')
const recoveredByRetry = new Counter('recovered_by_same_key_retry')
const changeAttemptsRefused = new Counter('change_attempts_refused')
const closedRejections = new Counter('rejected_voting_closed')
const unexpectedErrors = new Counter('unexpected_errors')
const receiptMismatches = new Counter('receipt_mismatches')
const socketsOpened = new Counter('sockets_opened')
const stateSignals = new Counter('state_signals')

function uuid() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

function unexpected(where, res) {
  unexpectedErrors.add(1)
  console.error(`${where}: HTTP ${res.status} ${String(res.body).slice(0, 200)}`)
}

function json(res) {
  try {
    return res.json()
  } catch (_) {
    return null
  }
}

export async function participant() {
  // Unique per participant VU (vu.idInTest also counts the host scenario).
  const index = exec.scenario.iterationInTest
  const userId = accounts[index % accounts.length]
  const behaviour = index % 20 === 0 ? 'lossy' : index % 10 === 1 ? 'duplicate' : 'normal'
  const headers = { Origin: ORIGIN, 'Content-Type': 'application/json' }

  await new Promise((resolve) => setTimeout(resolve, Math.random() * LOGIN_RAMP_SECONDS * 1000))
  const login = await http.asyncRequest('POST', `${TARGET}/api/v1/auth/login`, JSON.stringify({ user_id: userId, password: PASSWORD }), {
    headers,
    tags: { name: 'login' },
  })
  loginLatency.add(login.timings.duration)
  if (login.status !== 200) {
    unexpected('login', login)
    return
  }

  let csrf = ''
  let stateVersion = -1
  const confirmed = {} // ballotId -> receipt_code
  const inFlight = {}
  let refreshTimer = null

  async function snapshot() {
    const res = await http.asyncRequest('GET', `${TARGET}/api/v1/me/snapshot`, null, { headers, tags: { name: 'snapshot' } })
    snapshotLatency.add(res.timings.duration)
    if (res.status !== 200) {
      unexpected('snapshot', res)
      return null
    }
    const data = json(res)
    csrf = data.csrf_token
    stateVersion = data.state_version
    return data
  }

  function confirm(ballotId, receipt) {
    if (!receipt) return
    if (confirmed[ballotId] && confirmed[ballotId] !== receipt.receipt_code) {
      receiptMismatches.add(1)
      console.error(`receipt changed for ${ballotId}: ${confirmed[ballotId]} -> ${receipt.receipt_code}`)
    }
    if (!confirmed[ballotId]) ballotsConfirmed.add(1)
    confirmed[ballotId] = receipt.receipt_code
  }

  async function post(ballotId, nomineeId, key, timeout) {
    return http.asyncRequest('POST', `${TARGET}/api/v1/me/ballots/${ballotId}/vote`, JSON.stringify({ nominee_id: nomineeId }), {
      headers: { ...headers, 'X-CSRF-Token': csrf, 'Idempotency-Key': key },
      tags: { name: 'vote' },
      timeout: timeout || '15s',
    })
  }

  function handleVoteResponse(ballotId, res, record) {
    const body = json(res)
    if (res.status === 201) {
      votesCreated.add(1)
      if (record) voteLatency.add(res.timings.duration)
      confirm(ballotId, body.receipt)
      return 'ok'
    }
    if (res.status === 200) {
      votesReplayed.add(1)
      confirm(ballotId, body.receipt)
      return 'ok'
    }
    const code = body && body.error ? body.error.code : ''
    if (res.status === 409 && (code === 'ALREADY_VOTED' || code === 'IDEMPOTENCY_KEY_REUSED')) {
      confirm(ballotId, body.error.details.receipt)
      return 'ok'
    }
    if (res.status === 409 && code === 'VOTING_CLOSED') {
      closedRejections.add(1)
      return 'closed'
    }
    if (res.status === 0) return 'lost'
    unexpected(`vote ${code}`, res)
    return 'error'
  }

  async function recover(ballotId, nomineeId, key) {
    for (let attempt = 0; attempt < 6; attempt++) {
      const r = await http.asyncRequest('GET', `${TARGET}/api/v1/me/ballots/${ballotId}/receipt`, null, {
        headers,
        tags: { name: 'receipt' },
      })
      if (r.status === 200) {
        recoveredByReceipt.add(1)
        confirm(ballotId, json(r).receipt)
        return
      }
      if (r.status !== 404) {
        unexpected('receipt', r)
        return
      }
      const retry = await post(ballotId, nomineeId, key)
      const outcome = handleVoteResponse(ballotId, retry, false)
      if (outcome === 'ok') {
        recoveredByRetry.add(1)
        return
      }
      if (outcome === 'closed' || outcome === 'error') return
      await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)))
    }
  }

  async function vote(eventId) {
    const res = await http.asyncRequest('GET', `${TARGET}/api/v1/me/events/${eventId}`, null, { headers, tags: { name: 'event' } })
    if (res.status !== 200) {
      unexpected('event', res)
      return
    }
    const detail = json(res)
    for (const ballot of detail.ballots) {
      if (ballot.receipt) {
        confirm(ballot.id, ballot.receipt)
        continue
      }
      if (confirmed[ballot.id] || inFlight[ballot.id]) continue
      inFlight[ballot.id] = true
      const nominee = ballot.nominees[Math.floor(Math.random() * ballot.nominees.length)]
      const key = uuid()
      if (behaviour === 'duplicate') {
        duplicatesDetected.add(1)
        const both = await Promise.all([post(ballot.id, nominee.id, key), post(ballot.id, nominee.id, key)])
        for (const r of both) handleVoteResponse(ballot.id, r, false)
      } else if (behaviour === 'lossy') {
        lostResponses.add(1)
        // The request is sent, but the client gives up before the answer arrives.
        const r = await post(ballot.id, nominee.id, key, `${2 + Math.floor(Math.random() * 14)}ms`)
        const outcome = handleVoteResponse(ballot.id, r, false)
        if (outcome === 'lost') await recover(ballot.id, nominee.id, key)
        // A later attempt to choose someone else must be refused without changes.
        const other = ballot.nominees.find((n) => n.id !== nominee.id) || nominee
        const change = await post(ballot.id, other.id, uuid())
        const changeBody = json(change)
        if (change.status === 409 && changeBody && changeBody.error.code === 'ALREADY_VOTED') {
          changeAttemptsRefused.add(1)
          confirm(ballot.id, changeBody.error.details.receipt)
        } else if (!(change.status === 409 && changeBody && changeBody.error.code === 'VOTING_CLOSED')) {
          unexpected('change attempt', change)
        }
      } else {
        const r = await post(ballot.id, nominee.id, key)
        const outcome = handleVoteResponse(ballot.id, r, true)
        if (outcome === 'lost') await recover(ballot.id, nominee.id, key)
      }
      inFlight[ballot.id] = false
    }
  }

  async function onState(data) {
    if (!data || !data.live_event_id) return
    const live = data.events.find((e) => e.id === data.live_event_id)
    if (!live || live.ballots.every((b) => b.voted || confirmed[b.id])) return
    // People take a few seconds to read and tap.
    await new Promise((resolve) => setTimeout(resolve, Math.random() * BURST_SECONDS * 1000))
    await vote(live.id)
  }

  const first = await snapshot()
  if (!first) return
  await onState(first)

  const cookieJar = http.cookieJar()
  const cookies = cookieJar.cookiesForURL(TARGET)
  const cookieHeader = Object.keys(cookies)
    .map((k) => `${k}=${cookies[k][0]}`)
    .join('; ')
  const wsUrl = TARGET.replace(/^http/, 'ws') + '/ws'
  const ws = new WebSocket(wsUrl, null, { headers: { Origin: ORIGIN, Cookie: cookieHeader } })
  ws.onopen = () => socketsOpened.add(1)
  ws.onmessage = (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch (_) {
      return
    }
    if (msg.type === 'state') {
      stateSignals.add(1)
      if (typeof msg.ts === 'number') statePropagation.add(Date.now() - msg.ts * 1000)
      if (typeof msg.v === 'number' && msg.v <= stateVersion) return
      if (refreshTimer) clearTimeout(refreshTimer)
      refreshTimer = setTimeout(async () => {
        const data = await snapshot()
        await onState(data)
      }, Math.random() * 700)
    } else if (msg.type === 'resync') {
      snapshot().then(onState)
    }
  }
  ws.onerror = (e) => {
    console.error(`websocket error: ${e.error}`)
    unexpectedErrors.add(1)
  }
  const ping = setInterval(() => {
    if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping', t: Date.now() }))
  }, 25000)

  await new Promise((resolve) => setTimeout(resolve, HOLD_SECONDS * 1000))
  clearInterval(ping)
  ws.close()

  // Every receipt this client was given must be what the server reports now.
  const final = await snapshot()
  if (final) {
    const stored = {}
    for (const r of final.receipts) stored[r.ballot_id] = r.receipt_code
    for (const ballotId of Object.keys(confirmed)) {
      if (stored[ballotId] !== confirmed[ballotId]) {
        receiptMismatches.add(1)
        console.error(`acknowledged receipt missing or different for ballot ${ballotId}`)
      }
    }
  }
}

export async function host() {
  const headers = { Origin: ORIGIN, 'Content-Type': 'application/json' }
  const login = http.post(
    `${TARGET}/api/v1/admin/auth/login`,
    JSON.stringify({ email: __ENV.ADMIN_EMAIL, password: __ENV.ADMIN_PASSWORD }),
    { headers, tags: { name: 'admin_login' } },
  )
  if (login.status !== 200) {
    unexpected('admin login', login)
    return
  }
  const me = http.get(`${TARGET}/api/v1/admin/me`, { headers }).json()
  const adminHeaders = { ...headers, 'X-CSRF-Token': me.csrf_token }
  await new Promise((resolve) => setTimeout(resolve, (LOGIN_RAMP_SECONDS + 10) * 1000))

  for (let round = 0; round < ROUNDS; round++) {
    const live = http.get(`${TARGET}/api/v1/admin/live`, { headers, tags: { name: 'admin_live' } }).json()
    const next = live.events.find((e) => e.status === 'upcoming')
    if (!next) break
    const deadline = new Date(Date.now() + (ROUND_SECONDS + 120) * 1000).toISOString()
    const action = live.current_event ? 'start-next' : 'open'
    const res = http.post(
      `${TARGET}/api/v1/admin/live/${action}`,
      JSON.stringify({ event_id: next.id, expected_live_version: live.live_version, deadline_at: deadline }),
      { headers: adminHeaders, tags: { name: `admin_${action}` } },
    )
    transitionLatency.add(res.timings.duration)
    if (res.status !== 200) unexpected(`admin ${action}`, res)
    else console.log(`round ${round + 1}: ${action} ${next.name}`)
    await new Promise((resolve) => setTimeout(resolve, ROUND_SECONDS * 1000))
  }
  const live = http.get(`${TARGET}/api/v1/admin/live`, { headers }).json()
  if (live.current_event) {
    const res = http.post(
      `${TARGET}/api/v1/admin/live/close`,
      JSON.stringify({ event_id: live.current_event.id, expected_live_version: live.live_version }),
      { headers: adminHeaders, tags: { name: 'admin_close' } },
    )
    transitionLatency.add(res.timings.duration)
    if (res.status !== 200) unexpected('admin close', res)
  }
}

function metric(data, name, stat) {
  const m = data.metrics[name]
  if (!m) return 'n/a'
  const v = m.values[stat]
  return typeof v === 'number' ? v.toFixed(stat === 'count' || stat === 'rate' ? 0 : 1) : 'n/a'
}

export function handleSummary(data) {
  const lines = [
    `voters=${VOTERS} rounds=${ROUNDS} burst=${BURST_SECONDS}s login_ramp=${LOGIN_RAMP_SECONDS}s`,
    `sockets_opened=${metric(data, 'sockets_opened', 'count')} state_signals=${metric(data, 'state_signals', 'count')}`,
    `ballots_confirmed=${metric(data, 'ballots_confirmed', 'count')} created_201=${metric(data, 'votes_created_201', 'count')} replayed_200=${metric(data, 'votes_replayed_200', 'count')}`,
    `duplicate_retries=${metric(data, 'duplicate_retries', 'count')} lost_responses=${metric(data, 'lost_responses_simulated', 'count')} recovered_by_receipt=${metric(data, 'recovered_by_receipt', 'count')} recovered_by_retry=${metric(data, 'recovered_by_same_key_retry', 'count')} change_refused=${metric(data, 'change_attempts_refused', 'count')}`,
    `rejected_voting_closed=${metric(data, 'rejected_voting_closed', 'count')} unexpected_errors=${metric(data, 'unexpected_errors', 'count')} receipt_mismatches=${metric(data, 'receipt_mismatches', 'count')}`,
    `vote_latency ms: p50=${metric(data, 'vote_latency', 'med')} p95=${metric(data, 'vote_latency', 'p(95)')} p99=${metric(data, 'vote_latency', 'p(99)')} max=${metric(data, 'vote_latency', 'max')}`,
    `state_propagation ms: p50=${metric(data, 'state_propagation', 'med')} p95=${metric(data, 'state_propagation', 'p(95)')} p99=${metric(data, 'state_propagation', 'p(99)')} max=${metric(data, 'state_propagation', 'max')}`,
    `snapshot_latency ms: p50=${metric(data, 'snapshot_latency', 'med')} p95=${metric(data, 'snapshot_latency', 'p(95)')} p99=${metric(data, 'snapshot_latency', 'p(99)')}`,
    `login_latency ms: p50=${metric(data, 'login_latency', 'med')} p95=${metric(data, 'login_latency', 'p(95)')} p99=${metric(data, 'login_latency', 'p(99)')}`,
    `transition_latency ms: p50=${metric(data, 'transition_latency', 'med')} max=${metric(data, 'transition_latency', 'max')}`,
    `http_reqs=${metric(data, 'http_reqs', 'count')} http_req_failed_rate=${data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate.toFixed(4) : 'n/a'}`,
  ]
  return {
    '/out/summary.json': JSON.stringify(data, null, 2),
    '/out/summary.txt': lines.join('\n') + '\n',
    stdout: lines.join('\n') + '\n',
  }
}
