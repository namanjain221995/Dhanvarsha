import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import zlib from 'node:zlib'
import { type Page, expect, request, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { readInvitation } from '../lib/mailpit'
import { invitedParticipant, linkToken, noHorizontalOverflow, uniqueEmail } from '../lib/participants'

/*
 * Organiser console checks. Live state is shared by every spec, so each test creates its
 * own events and participants (through the UI or the admin API) instead of relying on the
 * demo rounds, and leaves the programme running.
 */

const ADMIN_EMAIL = () => process.env.ADMIN_EMAIL ?? ''
const ADMIN_PASSWORD = () => process.env.ADMIN_PASSWORD ?? ''
const tag = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`

// ---- helpers (spec-local) -------------------------------------------------------

/** Admin API calls the shared helper does not cover (same login, CSRF header). */
async function adminApi(baseURL: string) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } })
  const login = await api.post('/api/v1/admin/auth/login', { data: { email: ADMIN_EMAIL(), password: ADMIN_PASSWORD() } })
  expect(login.status(), await login.text()).toBe(200)
  const me = await (await api.get('/api/v1/admin/me')).json()
  const call = async <T = Record<string, unknown>>(method: 'get' | 'post' | 'patch', path: string, data?: unknown): Promise<T> => {
    const res = await api[method](`/api/v1/admin${path}`, {
      data,
      headers: method === 'get' ? undefined : { 'X-CSRF-Token': me.csrf_token as string },
    })
    const text = await res.text()
    if (!res.ok()) throw new Error(`${method.toUpperCase()} ${path} -> ${res.status()} ${text}`)
    return (text ? JSON.parse(text) : {}) as T
  }
  return { call, dispose: () => api.dispose() }
}

/** Events can only open inside the programme access window; keep it open for a while. */
async function ensureAccessOpen(admin: Admin) {
  const p = (await admin.programme()) as unknown as { login_phase?: string; login_closes_at?: string | null }
  const closesSoon = !p.login_closes_at || Date.parse(p.login_closes_at) < Date.now() + 2 * 3600_000
  if (p.login_phase !== 'open' || closesSoon) {
    await admin.patchProgramme({
      login_opens_at: new Date(Date.now() - 3600_000).toISOString(),
      login_closes_at: new Date(Date.now() + 8 * 3600_000).toISOString(),
    })
  }
}

/** Sign the browser context in as the organiser through the API (fast path). */
async function organiserSession(page: Page, baseURL: string) {
  const res = await page.request.post('/api/v1/admin/auth/login', {
    headers: { Origin: baseURL },
    data: { email: ADMIN_EMAIL(), password: ADMIN_PASSWORD() },
  })
  expect(res.status(), await res.text()).toBe(200)
}

/** A real 8×8 PNG, generated in memory (no fixture files). */
function tinyPng(rgb: [number, number, number] = [91, 52, 196]): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc32 = (data: Buffer) => {
    let c = 0xffffffff
    for (const byte of data) c = crcTable[(c ^ byte) & 0xff]! ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(body))
    return Buffer.concat([length, body, crc])
  }
  const size = 8
  const raw = Buffer.alloc((size * 3 + 1) * size)
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) raw.set(rgb, y * (size * 3 + 1) + 1 + x * 3)
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // truecolour RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/**
 * Sign a participant in through the API with their link (as its button does; the request
 * context keeps the session and device cookies) and vote for `nomineeName` on the first ballot.
 */
async function voteAs(baseURL: string, person: { link: string }, eventId: string, nomineeName: string) {
  const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL } })
  const enter = await api.post('/api/v1/auth/link', { data: { token: linkToken(person.link) } })
  expect(enter.status(), await enter.text()).toBe(200)
  const snapshot = await (await api.get('/api/v1/me/snapshot')).json()
  const detail = await (await api.get(`/api/v1/me/events/${eventId}`)).json()
  const ballot = detail.ballots[0] as { id: string; nominees: { id: string; name: string }[] }
  const nominee = ballot.nominees.find((n) => n.name === nomineeName)!
  const vote = await api.post(`/api/v1/me/ballots/${ballot.id}/vote`, {
    data: { nominee_id: nominee.id },
    headers: { 'X-CSRF-Token': snapshot.csrf_token, 'Idempotency-Key': randomUUID() },
  })
  expect(vote.status(), await vote.text()).toBe(201)
  return api
}

async function flash(page: Page, text: string | RegExp) {
  await expect(page.locator('[aria-live]').filter({ hasText: text }).first()).toBeVisible()
}

// ---- desktop flows ----------------------------------------------------------------

test.describe('organiser console', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-chromium', 'admin flows run on desktop'))

  test('sign-in through the form, unauthenticated redirect and sign-out', async ({ page }) => {
    await page.goto('/admin/participants')
    await expect(page).toHaveURL(/\/admin\/login\?next=%2Fadmin%2Fparticipants$/)
    await expect(page.getByRole('heading', { name: 'Organiser sign-in' })).toBeVisible()

    await page.getByLabel('Email').fill(ADMIN_EMAIL())
    await page.getByLabel('Password', { exact: true }).fill('definitely not the password')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.locator('form').getByRole('alert')).toContainText('That email or password is not right')
    await expect(page.getByLabel('Password', { exact: true })).toHaveValue('')

    await page.getByLabel('Password', { exact: true }).fill(ADMIN_PASSWORD())
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/admin\/participants$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Participants' })).toBeVisible()
    await expect(page.getByRole('complementary').or(page.locator('aside')).first()).toContainText(ADMIN_EMAIL())

    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/admin\/login\?signed_out=1$/)
    await expect(page.getByText('You signed out of the organiser console.')).toBeVisible()
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/admin\/login\?next=%2Fadmin$/)
  })

  test('programme settings save, logo upload and stale-settings handling', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const before = (await admin.programme()) as unknown as {
      help_contact: string
      accent: string
      preview_upcoming: boolean
      logo_asset_id: string | null
    }
    await organiserSession(page, baseURL!)
    await page.goto('/admin/programme')
    await expect(page.getByRole('heading', { level: 1, name: 'Programme settings' })).toBeVisible()

    const help = `the e2e help desk ${tag()}`
    await page.getByLabel('Where to get help').fill(help)
    await page.getByText('teal', { exact: true }).click()
    const previewSwitch = page.getByRole('switch', { name: 'Show upcoming event names' })
    const previewWas = (await previewSwitch.getAttribute('aria-checked')) === 'true'
    await previewSwitch.click()
    await expect(previewSwitch).toHaveAttribute('aria-checked', String(!previewWas))

    const logo = page.getByRole('group', { name: 'Logo' })
    await logo.locator('input[type=file]').setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: tinyPng() })
    await expect(logo.locator('img[src^="/media/"]')).toHaveCount(1)

    await expect(page.getByText('You have unsaved changes')).toBeVisible()
    await page.getByRole('button', { name: 'Save changes' }).click()
    await flash(page, 'Settings saved')
    await expect(page.getByText('You have unsaved changes')).toBeHidden()

    await page.reload()
    await expect(page.getByLabel('Where to get help')).toHaveValue(help)
    await expect(page.getByRole('radio', { name: /teal/i })).toBeChecked()
    await expect(page.getByRole('group', { name: 'Logo' }).locator('img[src^="/media/"]')).toHaveCount(1)
    const saved = (await admin.programme()) as unknown as { help_contact: string; accent: string; logo_url: string | null }
    expect(saved.help_contact).toBe(help)
    expect(saved.accent).toBe('teal')
    expect(saved.logo_url).toMatch(/^\/media\//)

    // Another organiser saves while this page has unsaved edits: never silently overwritten.
    await page.getByLabel('Where to get help').fill(`${help} (local edit)`)
    const theirs = `changed elsewhere ${tag()}`
    await admin.patchProgramme({ help_contact: theirs })
    await expect(page.getByText('Another organiser changed these settings')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await page.getByRole('button', { name: 'Load their version' }).click()
    await expect(page.getByLabel('Where to get help')).toHaveValue(theirs)

    await admin.patchProgramme({
      help_contact: before.help_contact,
      accent: before.accent,
      preview_upcoming: before.preview_upcoming,
      ...(before.logo_asset_id ? { logo_asset_id: before.logo_asset_id } : { clear_logo: true }),
    })
    await admin.dispose()
  })

  test('participant import preview counts, import and invitation email', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const existing = uniqueEmail('existing')
    await admin.importParticipants([`Existing Person <${existing}>`])
    const first = uniqueEmail('import-a')
    const second = uniqueEmail('import-b')

    await organiserSession(page, baseURL!)
    await page.goto('/admin/participants')
    await page.getByRole('button', { name: 'Add people', exact: true }).click()
    await page
      .getByLabel('Email addresses')
      .fill([`Import Alpha <${first}>`, `${second}, Import Beta`, 'not-an-email', first, existing].join('\n'))
    await page.getByRole('button', { name: 'Check addresses' }).click()

    const panel = page.getByRole('region', { name: 'Add participants' })
    await expect(panel.getByText('2 new participants ready to import.')).toBeVisible()
    await expect(panel.getByText('New: 2')).toBeVisible()
    await expect(panel.getByText('Invalid: 1')).toBeVisible()
    await expect(panel.getByText('Duplicate in list: 1')).toBeVisible()
    await expect(panel.getByText('Already imported: 1')).toBeVisible()
    await expect(panel.getByText('Same address as row 1.').filter({ visible: true })).toBeVisible()

    await panel.getByRole('button', { name: 'Import 2 participants' }).click()
    await flash(page, 'Imported 2 participants')

    await page.getByLabel('Search').fill(first)
    const manage = page.getByRole('button', { name: 'Manage Import Alpha' })
    await expect(manage).toBeVisible()
    await expect(page.getByRole('row', { name: /Import Alpha/ })).toContainText('Not invited')
    await manage.click()

    const dialog = page.getByRole('dialog', { name: 'Import Alpha' })
    await dialog.getByRole('button', { name: 'Send invitation…' }).click()
    await expect(dialog.getByText(/Emails a new personal sign-in link/)).toBeVisible()
    await dialog.getByRole('button', { name: 'Send invitation', exact: true }).click()
    await expect(dialog.getByText('Invitation queued for Import Alpha.')).toBeVisible()

    // The email carries their personal link (readInvitation accepts only a real token) and no
    // user ID or password.
    const invitation = await readInvitation(first)
    expect(invitation.link).toBe(`${baseURL}/enter#token=${invitation.token}`)
    expect(invitation.text).not.toContain('User ID')
    expect(invitation.text).not.toContain('Password:')
    await dialog.getByRole('button', { name: 'Done' }).click()
    await expect(page.getByRole('row', { name: /Import Alpha/ })).toContainText(/Invit/)
    await admin.dispose()
  })

  test('event creation with ballots and nominees in the editor', async ({ page, baseURL }) => {
    await organiserSession(page, baseURL!)
    const name = `E2E Editor ${tag()}`
    await page.goto('/admin/events')
    await page.getByRole('button', { name: 'New event' }).click()
    const create = page.getByRole('dialog', { name: 'New event' })
    await create.getByLabel('Event name').fill(name)
    await create.getByLabel('Ballot title').fill('Best team')
    await create.getByRole('button', { name: 'Create event' }).click()

    await expect(page).toHaveURL(/\/admin\/events\/[0-9a-f-]{36}\?created=1$/)
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
    await expect(page.getByText('Not ready to open yet')).toBeVisible()

    await page.getByRole('button', { name: 'Add nominee to Best team' }).click()
    for (const nominee of ['Nominee One', 'Nominee Two']) {
      await page.getByLabel('Team name').fill(nominee)
      await page.getByLabel('Organisation or school').fill('E2E Academy')
      await page.getByLabel('Team members').fill(`${nominee} Lead, ${nominee} Second\n${nominee} Third`)
      await page.getByRole('button', { name: 'Add nominee', exact: true }).click()
      await flash(page, `${nominee} added to Best team.`)
      await expect(page.getByLabel('Team name')).toHaveValue('')
    }
    await page.getByRole('button', { name: 'Done' }).click()
    const ballot = page.getByRole('region', { name: 'Best team' })
    await expect(ballot.getByRole('listitem')).toHaveCount(2)
    await expect(ballot.getByText('Nominee One Lead, Nominee One Second & Nominee One Third')).toBeVisible()

    await page.getByRole('button', { name: 'Move Nominee Two up' }).click()
    await flash(page, 'Nominee Two is now number 1 on Best team.')
    await expect(ballot.getByRole('listitem').first()).toContainText('Nominee Two')

    await page.getByRole('button', { name: 'Add another ballot' }).click()
    await page.getByLabel('Ballot title').fill('Best member')
    await page.getByLabel('Ballot type').selectOption({ label: 'Person vote' })
    await page.getByRole('button', { name: 'Add ballot', exact: true }).click()
    await flash(page, 'Ballot “Best member” added.')
    await page.getByRole('button', { name: 'Add nominee to Best member' }).click()
    await expect(page.getByRole('region', { name: 'Best member' }).getByLabel('Team members')).toHaveCount(0)
    await page.getByRole('region', { name: 'Best member' }).getByLabel('Name', { exact: true }).fill('Solo Performer')
    await page.getByRole('button', { name: 'Add nominee', exact: true }).click()
    await flash(page, 'Solo Performer added to Best member.')
    await expect(page.getByText('Not ready to open yet')).toBeHidden()

    await page.getByRole('button', { name: 'Preview' }).click()
    const preview = page.getByRole('dialog', { name: 'Participant preview' })
    await expect(preview.getByText(name)).toBeVisible()
    await expect(preview.getByText('Nominee Two', { exact: true })).toBeVisible()
    await expect(preview.getByText('Nominee Two Lead, Nominee Two Second & Nominee Two Third')).toBeVisible()
    await expect(preview.getByText('Solo Performer')).toBeVisible()
    await preview.getByRole('button', { name: 'Close' }).click()

    await page.goto('/admin/events')
    await expect(page.getByRole('link', { name: new RegExp(name) }).first()).toBeVisible()
  })

  test('live control: open, stale state refused, start next, close voting', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const api = await adminApi(baseURL!)
    await admin.ensureRunning()
    await ensureAccessOpen(admin)
    await admin.closeCurrent()
    const suffix = tag()
    const nameA = `E2E Live A ${suffix}`
    const nameB = `E2E Live B ${suffix}`
    const eventA = await admin.createEvent(nameA, ['Amber', 'Basalt'])
    const eventB = await admin.createEvent(nameB, ['Cobalt', 'Dune'])

    await organiserSession(page, baseURL!)
    await page.goto('/admin')
    await expect(page.getByRole('heading', { level: 1, name: 'Live control' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'No event is open for voting' })).toBeVisible()

    // Open A from its row, with a deadline 15 minutes from now.
    await page.getByRole('button', { name: `Open ${nameA}…` }).click()
    const open = page.getByRole('dialog', { name: 'Open voting' })
    await expect(open.getByLabel('Event to open')).toHaveValue(eventA)
    await open.getByLabel('A number of minutes from now').check()
    await open.getByRole('button', { name: 'Open voting' }).click()
    await flash(page, `Voting is open for ${nameA}.`)
    await expect(page.getByRole('heading', { level: 2, name: nameA })).toBeVisible()
    await expect(page.getByText('Live now').first()).toBeVisible()

    // Another organiser changes the live state while the dialog is open: refused, refetched.
    await page.getByRole('button', { name: 'Start next event…' }).click()
    const next = page.getByRole('dialog', { name: 'Start the next event' })
    await next.getByLabel('Event to open').selectOption(eventB)
    const live = await admin.live()
    await api.call('post', '/live/deadline', {
      event_id: eventA,
      expected_live_version: live.live_version,
      deadline_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    })
    await expect(next.getByText('The live state changed while this was open')).toBeVisible()
    await next.getByRole('button', { name: 'Start next event', exact: true }).click()
    await flash(page, 'Another organiser or the schedule changed the live state. Review and try again.')
    await expect(next).toBeHidden()
    expect((await admin.live()).current_event?.id).toBe(eventA)

    // Try again with the fresh state: A closes and B opens in one step.
    await page.getByRole('button', { name: 'Start next event…' }).click()
    const retry = page.getByRole('dialog', { name: 'Start the next event' })
    await retry.getByLabel('Event to open').selectOption(eventB)
    await expect(retry).toContainText(nameA)
    await retry.getByRole('button', { name: 'Start next event', exact: true }).click()
    await flash(page, `${nameA} is closed. Voting is open for ${nameB}.`)
    await expect(page.getByRole('heading', { level: 2, name: nameB })).toBeVisible()

    // Close voting through the confirmation dialog.
    await page.getByRole('button', { name: 'Close voting…' }).click()
    const close = page.getByRole('dialog', { name: 'Close voting?' })
    await expect(close).toContainText('Votes already cast are final')
    await close.getByRole('button', { name: 'Close voting', exact: true }).click()
    await flash(page, `Voting closed for ${nameB}.`)
    await expect(page.getByRole('heading', { name: 'No event is open for voting' })).toBeVisible()

    const after = await admin.live()
    expect(after.current_event).toBeNull()
    expect(after.events.find((e) => e.id === eventA)?.status).toBe('closed')
    expect(after.events.find((e) => e.id === eventB)?.status).toBe('closed')
    await api.dispose()
    await admin.dispose()
  })

  test('results: live counts, tie label, CSV export and publishing to participants', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    await admin.ensureRunning()
    await ensureAccessOpen(admin)
    const name = `E2E Results ${tag()}`
    const eventId = await admin.createEvent(name, ['Ruby', 'Sapphire', 'Topaz'])
    const alice = await invitedParticipant(admin, baseURL!, 'results-a')
    const bob = await invitedParticipant(admin, baseURL!, 'results-b')
    await admin.open(eventId)

    await organiserSession(page, baseURL!)
    await page.goto(`/admin/results?event=${eventId}`)
    await expect(page.getByRole('heading', { level: 2, name })).toBeVisible()
    await expect(page.getByText('Provisional — voting open')).toBeVisible()
    await expect(page.getByText('No votes yet.')).toBeVisible()

    // Votes arrive while the page is open (tally signals, coalesced refetch).
    const aliceApi = await voteAs(baseURL!, alice, eventId, 'Ruby')
    const bobApi = await voteAs(baseURL!, bob, eventId, 'Sapphire')
    await expect(page.getByText('Tie: Ruby and Sapphire, 1 vote each.')).toBeVisible({ timeout: 20_000 })
    const topaz = page.getByRole('listitem').filter({ hasText: 'Topaz' })
    await expect(topaz).toContainText('0 votes')
    // A tie has no Audience Favourite, while open or after closing.
    await expect(page.getByText('Audience Favourite', { exact: true })).toHaveCount(0)

    await admin.closeCurrent()
    await expect(page.getByText('Final — voting closed')).toBeVisible()
    await expect(page.getByText(/Tie: Ruby and Sapphire, 1 vote each\. There is no single winner\./)).toBeVisible()
    await expect(page.getByText('Audience Favourite', { exact: true })).toHaveCount(0)

    // CSV: aggregate counts only.
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('link', { name: 'CSV for this event' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('stagevote-results.csv')
    const csv = await readFile((await download.path())!, 'utf8')
    const [header, ...rows] = csv.trim().split(/\r?\n/)
    expect(header).toBe(
      'round,event,event_status,result_label,ballot,nominee,affiliation,votes,percent,ballot_total_votes,eligible_voters,leading,tie',
    )
    expect(rows).toHaveLength(3)
    expect(rows.find((row) => row.includes(',Ruby,'))).toMatch(/,1,50\.0,2,\d+,yes,yes$/)
    expect(rows.find((row) => row.includes(',Topaz,'))).toMatch(/,0,0\.0,2,\d+,no,yes$/)
    for (const secret of [alice.email, bob.email, linkToken(alice.link), linkToken(bob.link), '@']) expect(csv).not.toContain(secret)

    // Not published yet: participants see no results.
    const hidden = await (await aliceApi.get(`/api/v1/me/events/${eventId}`)).json()
    expect(hidden.results).toBeNull()

    await page.getByRole('button', { name: 'Publish results…' }).click()
    const publish = page.getByRole('dialog', { name: 'Publish these results?' })
    await publish.getByRole('button', { name: 'Publish results' }).click()
    await flash(page, `Results for ${name} are published.`)
    await expect(page.getByText('Published to participants')).toBeVisible()

    const shown = await (await bobApi.get(`/api/v1/me/events/${eventId}`)).json()
    const ballots = shown.results as { nominees: { name: string; votes: number }[] }[]
    expect(ballots[0]!.nominees.find((n) => n.name === 'Ruby')?.votes).toBe(1)
    expect(ballots[0]!.nominees.find((n) => n.name === 'Sapphire')?.votes).toBe(1)
    await aliceApi.dispose()
    await bobApi.dispose()
    await admin.dispose()
  })

  test('email: diagnostics, edited invitation wording and real invitations to several addresses', async ({ page, baseURL }) => {
    await organiserSession(page, baseURL!)
    await page.goto('/admin/email')
    await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible()
    await expect(page.getByText('“Accepted” is not the same as “delivered”')).toBeVisible()

    await page.getByRole('button', { name: 'Run diagnostics' }).click()
    await expect(page.getByText(/: (OK|Warning|Error|Skipped)$/).first()).toBeVisible()

    // Edit the wording; the live preview follows the unsaved draft.
    const editor = page.getByRole('region', { name: 'Invitation email' })
    await editor.getByLabel('Subject').fill('{programme}: your invitation (e2e)')
    await editor.getByLabel('Message').fill('Dear {name},\n\nWelcome to the e2e night. <b>No markup</b> here.')
    await expect(editor.getByText(/Subject: .+: your invitation \(e2e\)/)).toBeVisible()
    const frame = editor.frameLocator('iframe[title="Invitation email preview"]')
    await expect(frame.getByText('Dear Asha Rao,', { exact: true })).toBeVisible()
    await expect(frame.getByText('Welcome to the e2e night. <b>No markup</b> here.', { exact: true })).toBeVisible()
    await editor.getByRole('button', { name: 'Save wording', exact: true }).click()
    await flash(page, 'Invitation wording saved.')

    // Real invitations straight from the Email page: new addresses become participants.
    const first = uniqueEmail('direct-a')
    const second = uniqueEmail('direct-b')
    await editor.getByLabel('Send invitations to').fill(`${first}, ${second}`)
    await editor.getByRole('button', { name: 'Send invitations', exact: true }).click()
    const confirm = page.getByRole('dialog', { name: 'Send 2 invitations?' })
    await confirm.getByRole('button', { name: 'Send invitations' }).click()
    await flash(page, '2 new participants added.')
    for (const to of [first, second]) {
      // Each one carries a real personal link, never the preview's example link.
      const invitation = await readInvitation(to)
      expect(invitation.link).not.toContain('#token=example')
      expect(invitation.text).toContain('Welcome to the e2e night.')
      expect(invitation.text).not.toContain('Preview')
      expect(invitation.html).not.toContain('<b>No markup</b>')
    }

    const row = page.getByRole('row').filter({ hasText: first })
    await expect(row).toBeVisible()
    await expect(row.getByText('Invitation', { exact: true })).toBeVisible()
    await expect(row.getByText('Accepted', { exact: true })).toBeVisible({ timeout: 30_000 })
    await row.getByRole('button', { name: `Attempts for ${first}` }).click()
    await expect(page.getByText(/Attempt 1: accepted/)).toBeVisible()

    // Back to the built-in wording so later invitations are unaffected.
    await editor.getByRole('button', { name: 'Use the built-in wording' }).click()
    await editor.getByRole('button', { name: 'Save wording', exact: true }).click()
    await flash(page, 'Invitations use the built-in wording again.')
  })
})

// ---- phone navigation ---------------------------------------------------------------

test.describe('organiser console on a phone', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'mobile-chromium', 'phone navigation drawer'))

  test('navigation drawer: opens, keeps focus, closes with Escape, navigates', async ({ page, baseURL }) => {
    await organiserSession(page, baseURL!)
    await page.goto('/admin')
    await expect(page.getByRole('heading', { level: 1, name: 'Live control' })).toBeVisible()
    await noHorizontalOverflow(page)

    const menu = page.getByRole('button', { name: 'Open navigation' })
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    await menu.click()
    const drawer = page.getByRole('dialog', { name: 'Navigation' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('button', { name: 'Close navigation' })).toBeFocused()
    await expect(drawer.getByRole('link', { name: 'Live control' })).toHaveAttribute('aria-current', 'page')

    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    await expect(menu).toBeFocused()

    await menu.click()
    await drawer.getByRole('link', { name: 'Participants' }).click()
    await expect(page).toHaveURL(/\/admin\/participants$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Participants' })).toBeVisible()
    await expect(drawer).toBeHidden()
    await noHorizontalOverflow(page)
  })
})
