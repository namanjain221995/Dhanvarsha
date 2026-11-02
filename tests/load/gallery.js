// The photo gallery under many users at once, for k6. Run it only through tests/load/gallery-run.sh, which
// starts an isolated Compose project and refuses any other target.
//
// USERS people arrive together. Each fills in the form with a selfie, sends PHOTOS photos through this server
// (the slower of the two ways a file can arrive; straight-to-S3 only asks this server to sign links), and looks
// around as a phone does: the People page, its round pictures, a search, someone's page and its thumbnails.
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const TARGET = __ENV.TARGET
const ORIGIN = __ENV.ORIGIN
const USERS = Number(__ENV.USERS || 200)
const PHOTOS = Number(__ENV.PHOTOS || 5)
const API = `${TARGET}/api/v1/gallery`
if (!TARGET || !ORIGIN) throw new Error('use tests/load/gallery-run.sh')

const selfie = open('/data/selfie.jpg', 'b')
const photo = new Uint8Array(open('/data/photo.jpg', 'b'))

const steps = {}
for (const name of ['form', 'upload_start', 'upload_chunk', 'upload_complete', 'people', 'search', 'person', 'picture']) {
  steps[name] = new Trend(`step_${name}`, true)
}
const failed = new Counter('steps_failed')
const uploaded = new Counter('photos_uploaded')

export const options = {
  noCookiesReset: true,
  discardResponseBodies: false,
  scenarios: { people: { executor: 'per-vu-iterations', vus: USERS, iterations: 1, maxDuration: '40m' } },
}

const json = { Origin: ORIGIN, 'Content-Type': 'application/json' }

function step(name, response, expected = 200) {
  steps[name].add(response.timings.duration)
  if (!check(response, { [`${name} ${expected}`]: (r) => r.status === expected })) {
    failed.add(1)
    if (failed.name && __ITER === 0) console.warn(`${name} -> ${response.status} ${String(response.body).slice(0, 120)}`)
    return false
  }
  return true
}

function sendPhoto(index) {
  // Different bytes each time: the gallery counts the same file only once.
  const body = new Uint8Array(photo.length + 32)
  body.set(photo)
  for (let i = 0; i < 32; i += 1) body[photo.length + i] = Math.floor(Math.random() * 256)
  const started = http.post(`${API}/uploads`, JSON.stringify({ name: `user${__VU}-photo${index}.jpg`, size: body.length, content_type: 'image/jpeg' }), { headers: json })
  if (!step('upload_start', started, 201)) return
  const { upload_id: id, chunk_size: chunkSize } = started.json()
  for (let offset = 0; offset < body.length; offset += chunkSize) {
    const chunk = body.slice(offset, Math.min(body.length, offset + chunkSize))
    const sent = http.put(`${API}/uploads/${id}?offset=${offset}`, chunk.buffer, {
      headers: { Origin: ORIGIN, 'Content-Type': 'application/octet-stream' },
      timeout: '180s',
    })
    if (!step('upload_chunk', sent)) return
  }
  if (step('upload_complete', http.post(`${API}/uploads/${id}/complete`, null, { headers: { Origin: ORIGIN }, timeout: '120s' }), 201)) uploaded.add(1)
}

function pictures(urls) {
  for (const url of urls.filter(Boolean).slice(0, 24)) {
    const answer = http.get(`${TARGET}${url}`, { responseType: 'none' })
    step('picture', answer)
  }
}

export default function () {
  sleep(Math.random() * 3) // they arrive within a few seconds of each other
  const form = http.post(
    `${API}/enrol`,
    { name: `Load User ${__VU}`, consent: 'true', selfie: http.file(selfie, 'selfie.jpg', 'image/jpeg') },
    { headers: { Origin: ORIGIN }, timeout: '180s' },
  )
  if (!step('form', form)) return
  for (let index = 0; index < PHOTOS; index += 1) {
    sendPhoto(index)
    const people = http.get(`${API}/people?limit=60`)
    if (step('people', people)) {
      const found = people.json().people
      pictures(found.map((person) => person.avatar_url))
      const chosen = found[Math.floor(Math.random() * found.length)]
      if (chosen) {
        const page = http.get(`${API}/people/${chosen.id}?limit=60`)
        if (step('person', page)) pictures(page.json().media.map((item) => item.thumb_url))
      }
    }
    step('search', http.get(`${API}/people?q=seed${__VU % 100}&limit=60`))
    sleep(1 + Math.random() * 2)
  }
}

export function handleSummary(data) {
  const lines = ['step                 count      p50      p95      max']
  for (const name of Object.keys(steps)) {
    const metric = data.metrics[`step_${name}`]
    if (metric) {
      const v = metric.values
      lines.push(`${name.padEnd(18)} ${String(v.count ?? 0).padStart(7)} ${v.med.toFixed(0).padStart(7)}ms ${v['p(95)'].toFixed(0).padStart(7)}ms ${v.max.toFixed(0).padStart(7)}ms`)
    }
  }
  lines.push(`photos uploaded: ${data.metrics.photos_uploaded?.values.count ?? 0}  steps failed: ${data.metrics.steps_failed?.values.count ?? 0}`)
  return { '/out/summary.json': JSON.stringify(data), stdout: `${lines.join('\n')}\n` }
}
