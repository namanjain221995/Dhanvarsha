import { type Page, devices, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { invitedParticipant, signIn } from '../lib/participants'

/*
 * Audience polls: the organiser-only note of a round (written when the round is made, edited
 * after it opened) shows in Live control and never reaches participants, and a closed person
 * vote names its Audience Favourite, with the ballot description, in the console and on phones.
 */

const tag = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`

async function organiserLogin(page: Page, baseURL: string) {
  const res = await page.request.post('/api/v1/admin/auth/login', {
    headers: { Origin: baseURL },
    data: { email: process.env.ADMIN_EMAIL ?? '', password: process.env.ADMIN_PASSWORD ?? '' },
  })
  expect(res.status(), await res.text()).toBe(200)
}

async function flash(page: Page, text: string | RegExp) {
  await expect(page.locator('[aria-live]').filter({ hasText: text }).first()).toBeVisible()
}

test.describe('audience polls', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-chromium', 'console flow; the voter uses a phone context'))

  test('organiser note in Live control only, Audience Favourite after closing', async ({ page, browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    await admin.ensureRunning()
    await admin.ensureAccessOpen()
    await admin.closeCurrent()
    const name = `E2E Poll ${tag()}`
    const question = 'Who impressed you most with their policy knowledge and answers?'
    const description = 'The official winner is decided by the highest bid.'
    const whenToShow = 'When to show: after both HR participants complete the challenge.'
    const instruction = 'Show the result as a fun audience preference.'
    const changed = 'Keep it open until the applause stops.'

    // The note is written when the round is made.
    await organiserLogin(page, baseURL!)
    await page.goto('/admin/events')
    await page.getByRole('button', { name: 'New event' }).click()
    const create = page.getByRole('dialog', { name: 'New event' })
    await create.getByLabel('Event name').fill(name)
    await create.getByLabel('Ballot type').selectOption({ label: 'Person vote' })
    await create.getByLabel('Ballot title').fill(question)
    await expect(create.getByText('Only organisers see this: when to open the poll and any special instructions.')).toBeVisible()
    await create.getByLabel('Organiser note').fill(`${whenToShow}\n${instruction}`)
    await create.getByRole('button', { name: 'Create event' }).click()
    await expect(page).toHaveURL(/\/admin\/events\/[0-9a-f-]{36}\?created=1$/)
    const eventId = /\/admin\/events\/([0-9a-f-]{36})/.exec(page.url())![1]!
    await expect(page.getByLabel('Organiser note')).toHaveValue(`${whenToShow}\n${instruction}`)

    const made = await admin.event(eventId)
    expect(made.event.organiser_note).toBe(`${whenToShow}\n${instruction}`)
    const ballotId = made.event.ballots[0]!.id
    await admin.addNominees(ballotId, ['Anita', 'Athar'])
    await admin.patchBallot(ballotId, { description })

    // Events list: the first line under the name; the rest stays in the editor.
    await page.goto('/admin/events')
    const row = page.getByRole('listitem').filter({ has: page.getByRole('link', { name: new RegExp(name) }) })
    await expect(row).toContainText(whenToShow)
    await expect(row).not.toContainText(instruction)

    // Live control: the whole note, line breaks kept, in the list of rounds that can open.
    await page.goto('/admin')
    await expect(page.getByRole('heading', { level: 1, name: 'Live control' })).toBeVisible()
    const upcoming = page.getByRole('region', { name: /^All rounds/ }).getByRole('note', { name: `Organiser note for ${name}` })
    await expect(upcoming).toContainText('Only you see this')
    expect(await upcoming.innerText()).toContain(`${whenToShow}\n${instruction}`)

    // Open the round: the note is on the live card. It stays editable after opening.
    await admin.open(eventId)
    await page.reload()
    const onAir = page.getByRole('region', { name, exact: true })
    await expect(onAir.getByRole('note', { name: `Organiser note for ${name}` })).toContainText(instruction)
    await page.goto(`/admin/events/${eventId}`)
    await expect(page.getByText('Voting has opened, so ballots and nominees are locked')).toBeVisible()
    await page.getByLabel('Organiser note').fill(`${whenToShow}\n${changed}`)
    await page.getByRole('button', { name: 'Save details' }).click()
    await flash(page, 'Event details saved.')
    expect((await admin.live()).events.find((event) => event.id === eventId)?.organiser_note).toBe(`${whenToShow}\n${changed}`)
    await page.goto('/admin')
    const liveNote = page.getByRole('region', { name, exact: true }).getByRole('note', { name: `Organiser note for ${name}` })
    await expect(liveNote).toContainText(changed)
    await expect(liveNote).not.toContainText(instruction)

    // A voter never sees the note: not on the page, not in what the API sends them.
    const person = await invitedParticipant(admin, baseURL!, 'polls')
    const phone = await browser.newContext({ ...devices['Pixel 7'], baseURL })
    const voter = await phone.newPage()
    await signIn(voter, person.link)
    await voter.goto(`/event/${eventId}`)
    await expect(voter.getByRole('radiogroup')).toBeVisible()
    for (const secret of ['When to show', changed, 'Only you see this']) await expect(voter.getByText(secret)).toHaveCount(0)
    for (const path of ['/api/v1/me/snapshot', `/api/v1/me/events/${eventId}`]) {
      const res = await voter.request.get(path)
      expect(res.status(), path).toBe(200)
      const body = await res.text()
      for (const secret of ['organiser_note', 'When to show', changed]) expect(body, path).not.toContain(secret)
    }
    await voter.getByText('Anita', { exact: true }).click()
    await voter.getByRole('button', { name: 'Submit vote' }).click()
    await expect(voter.getByRole('region', { name: /Receipt for/ })).toContainText('Anita')

    // Closed: the console names the Audience Favourite, never a winner, under the description.
    await admin.closeCurrent()
    await page.goto(`/admin/results?event=${eventId}`)
    await expect(page.getByText('Final — voting closed')).toBeVisible()
    const card = page.getByRole('region', { name: question })
    await expect(card.getByText(description)).toBeVisible()
    await expect(card.getByText('Audience Favourite: Anita, 1 vote.')).toBeVisible()
    await expect(card.getByRole('listitem').filter({ hasText: 'Anita' })).toContainText('Audience Favourite')
    await expect(card.getByText('Winner', { exact: true })).toHaveCount(0)
    const results = await admin.results(eventId)
    expect(results.events[0]!.ballots[0]!.description).toBe(description)

    // Published: phones say the same, with the description, and still no note.
    await admin.publish(eventId)
    await voter.goto(`/event/${eventId}`)
    const shown = voter.getByRole('region', { name: 'Results' })
    await expect(shown).toContainText('Audience Favourite: Anita.')
    await expect(shown).toContainText(description)
    await expect(shown).not.toContainText('Most votes')
    await expect(voter.getByText('When to show')).toHaveCount(0)

    await phone.close()
    await admin.dispose()
  })
})
