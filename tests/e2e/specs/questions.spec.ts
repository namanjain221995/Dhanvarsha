import { type Page, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { invitedParticipant, noHorizontalOverflow, signIn } from '../lib/participants'

/*
 * Question rounds: a multiple-choice question and a typed-answer question in one round, and a
 * round without voting. Voters run on phones; the console checks run on desktop.
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

test.describe('question rounds on a phone', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name === 'desktop-chromium', 'covered on phones'))

  test('multiple-choice and typed answers, receipts, a round without voting, answers in results', async ({
    page,
    baseURL,
    browser,
  }) => {
    const admin = await Admin.login(baseURL!)
    await admin.ensureRunning()
    await admin.ensureAccessOpen()
    await admin.closeCurrent()
    const suffix = tag()
    const mcqTitle = 'Which planet is called the red planet?'
    const textTitle = 'What should the band play next?'
    const quiz = await admin.createRound(`Quiz ${suffix}`, [
      { kind: 'mcq', title: mcqTitle, options: ['Venus', 'Mars', 'Jupiter'] },
      { kind: 'text', title: textTitle },
    ])
    const show = await admin.createRound(`Show ${suffix}`, [])

    const voter = await invitedParticipant(admin, baseURL!, 'questions')
    await signIn(page, voter.link)

    // A round with only questions asks for answers, not votes.
    await admin.open(quiz.eventId)
    const liveCard = page.getByRole('link', { name: /Answer now/ })
    await expect(liveCard).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('link', { name: /Vote now/ })).toHaveCount(0)
    await liveCard.click()
    await expect(page).toHaveURL(new RegExp(`/event/${quiz.eventId}$`))
    await noHorizontalOverflow(page)

    // Multiple choice: lettered answer cards, one radio group, nothing sent until submitted.
    const choices = page.getByRole('radiogroup', { name: mcqTitle })
    await expect(choices.getByRole('radio')).toHaveCount(3)
    await expect(choices.getByText('Choose one answer. Your answer is final once submitted.')).toBeVisible()
    await expect(choices.getByRole('radio', { name: /^A\. Venus/ })).toBeVisible()
    const submit = page.getByRole('button', { name: 'Submit answer' })
    await expect(submit).toBeDisabled()
    await choices.getByText('Mars', { exact: true }).click()
    await expect(choices.getByRole('radio', { name: /^B\. Mars/ })).toBeChecked()
    await expect(submit).toBeEnabled()
    await submit.click()
    const mcqReceipt = page.getByRole('region', { name: `Receipt for ${mcqTitle}` })
    await expect(mcqReceipt).toContainText('Answer recorded')
    await expect(mcqReceipt).toContainText('Mars')
    await expect(choices).toHaveCount(0)
    // One question still open, so the page stays.
    await expect(page.getByText('Going back to the programme…')).toHaveCount(0)

    // Typed answer: a textarea named by the question, with a counter, blank answers refused.
    const box = page.getByRole('textbox', { name: textTitle })
    await expect(box).toBeVisible()
    await expect(box).toHaveAttribute('maxlength', '500')
    await expect(page.getByText('Type your answer. It is final once submitted.')).toBeVisible()
    await expect(page.getByText('0/500')).toBeVisible()
    await expect(submit).toBeDisabled()
    await box.fill('   ')
    await expect(page.getByText('3/500')).toBeVisible()
    await expect(submit).toBeDisabled()
    const answer = 'Play the encore again, please!'
    const typed = `  ${answer}\n`
    await box.fill(typed)
    await expect(page.getByText(`${typed.length}/500`)).toBeVisible()
    await expect(submit).toBeEnabled()

    // The draft survives a reload, like a vote draft, and is still not sent by itself.
    await page.reload()
    await expect(page.getByRole('textbox', { name: textTitle })).toHaveValue(typed)
    await expect(page.getByRole('region', { name: `Receipt for ${textTitle}` })).toHaveCount(0)
    await page.getByRole('button', { name: 'Submit answer' }).click()
    const textReceipt = page.getByRole('region', { name: `Receipt for ${textTitle}` })
    await expect(textReceipt).toContainText('Answer recorded')
    await expect(textReceipt).toContainText(answer)
    await expect(page.getByRole('textbox')).toHaveCount(0)
    // The last answer of the round: back to the programme after a few seconds.
    await expect(page.getByText('Going back to the programme…')).toBeVisible()
    await expect(page).toHaveURL(/\/lobby$/, { timeout: 8_000 })
    await expect(page.getByRole('link', { name: /View your answers/ })).toBeVisible()

    // A round without voting: no button to vote or answer, and the round page says so.
    await admin.open(show.eventId)
    const showCard = page.getByRole('link', { name: /No voting in this round/ })
    await expect(showCard).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('link', { name: /Vote now|Answer now/ })).toHaveCount(0)
    await expect(page.getByText('Answers recorded').first()).toBeVisible() // the quiz row, now closed
    await showCard.click()
    await expect(page).toHaveURL(new RegExp(`/event/${show.eventId}$`))
    await expect(page.getByText('No voting in this round. Enjoy the show!')).toBeVisible()
    await expect(page.getByRole('button', { name: /^Submit/ })).toHaveCount(0)
    await expect(page.getByRole('radio')).toHaveCount(0)
    await page.waitForTimeout(4_000)
    await expect(page).toHaveURL(new RegExp(`/event/${show.eventId}$`)) // no auto-return without voting

    // Organiser: option counts, and the typed answer with who typed it (trimmed).
    const results = await admin.results(quiz.eventId)
    const ballots = results.events[0]!.ballots
    const mcqResult = ballots.find((b) => b.kind === 'mcq')!
    expect(mcqResult.nominees.find((n) => n.name === 'Mars')?.votes).toBe(1)
    expect(mcqResult.nominees.find((n) => n.name === 'Venus')?.votes).toBe(0)
    const textResult = ballots.find((b) => b.kind === 'text')!
    expect(textResult.answer_count).toBe(1)
    expect(textResult.answers?.map((a) => a.answer)).toEqual([answer])
    expect(textResult.answers?.[0]?.participant_email).toBe(voter.email)

    const desk = await browser.newContext({ baseURL, viewport: { width: 1366, height: 900 }, isMobile: false, hasTouch: false })
    const organiser = await desk.newPage()
    await organiserLogin(organiser, baseURL!)
    await organiser.goto(`/admin/results?event=${quiz.eventId}`)
    const typedCard = organiser.getByRole('region', { name: textTitle })
    await expect(typedCard).toContainText('Typed-answer question')
    await expect(typedCard.getByRole('cell', { name: answer })).toBeVisible()
    await expect(typedCard.getByRole('cell', { name: voter.email })).toBeVisible()
    await expect(organiser.getByRole('region', { name: mcqTitle })).toContainText('Multiple-choice question')
    await desk.close()

    await admin.closeCurrent()
    await admin.ensureRunning()
    await admin.dispose()
  })
})

test.describe('question rounds in the console', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-chromium', 'admin flows run on desktop'))

  test('organiser builds a quiz round and a round without voting', async ({ page, baseURL }) => {
    await organiserLogin(page, baseURL!)
    const name = `E2E Quiz ${tag()}`
    const mcqTitle = 'Which colour is the stage curtain?'
    const textTitle = 'Any message for the performers?'

    await page.goto('/admin/events')
    await page.getByRole('button', { name: 'New event' }).click()
    const create = page.getByRole('dialog', { name: 'New event' })
    await create.getByLabel('Event name').fill(name)
    await create.getByLabel('Ballot type').selectOption({ label: 'Multiple-choice question' })
    await create.getByLabel('Question').fill(mcqTitle)
    await create.getByRole('button', { name: 'Create event' }).click()
    await expect(page).toHaveURL(/\/admin\/events\/[0-9a-f-]{36}\?created=1$/)
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
    await expect(page.getByText(`Add at least two answers to “${mcqTitle}”`)).toBeVisible()

    // Answer options: a name and an optional description, nothing else.
    const question = page.getByRole('region', { name: mcqTitle })
    await expect(question).toContainText('Multiple-choice question')
    await question.getByRole('button', { name: `Add answer option to ${mcqTitle}` }).click()
    await expect(question.getByLabel('Team or organisation')).toHaveCount(0)
    await expect(question.getByLabel('Team members')).toHaveCount(0)
    for (const option of ['Crimson', 'Gold']) {
      await question.getByLabel('Answer option', { exact: true }).fill(option)
      await question.getByRole('button', { name: 'Add option', exact: true }).click()
      await flash(page, `${option} added to ${mcqTitle}`)
      await expect(question.getByLabel('Answer option', { exact: true })).toHaveValue('')
    }
    await question.getByRole('button', { name: 'Done' }).click()
    await expect(question.getByRole('listitem')).toHaveCount(2)
    await expect(page.getByText('Not ready to open yet')).toBeHidden()

    // A question with options cannot become a typed-answer question.
    await question.getByRole('button', { name: /Edit question/ }).click()
    await expect(question.getByLabel('Ballot type').locator('option', { hasText: 'Typed-answer question' })).toBeDisabled()
    await question.getByRole('button', { name: 'Cancel' }).click()

    // A typed-answer question has no options editor.
    await page.getByRole('button', { name: 'Add another ballot' }).click()
    await page.getByLabel('Ballot type').selectOption({ label: 'Typed-answer question' })
    await page.getByLabel('Question', { exact: true }).fill(textTitle)
    await page.getByRole('button', { name: 'Add ballot', exact: true }).click()
    await flash(page, `Question “${textTitle}” added.`)
    const typed = page.getByRole('region', { name: textTitle })
    await expect(typed.getByText('Voters type their answer (up to 500 characters).')).toBeVisible()
    await expect(typed.getByRole('button', { name: /^Add / })).toHaveCount(0)

    await page.getByRole('button', { name: 'Preview' }).click()
    const preview = page.getByRole('dialog', { name: 'Participant preview' })
    await expect(preview.getByText('Crimson', { exact: true })).toBeVisible()
    await expect(preview.getByText(textTitle)).toBeVisible()
    await expect(preview.getByText('Type your answer here')).toBeVisible()
    await preview.getByRole('button', { name: 'Close' }).click()

    // A round without voting opens as it is.
    const showName = `E2E Show ${tag()}`
    await page.goto('/admin/events')
    await page.getByRole('button', { name: 'New event' }).click()
    await create.getByLabel('Event name').fill(showName)
    await create.getByLabel('Ballot type').selectOption({ label: 'No voting (show only)' })
    await expect(create.getByLabel('Ballot title')).toHaveCount(0)
    await expect(create.getByLabel('Question')).toHaveCount(0)
    await create.getByRole('button', { name: 'Create event' }).click()
    await expect(page.getByRole('heading', { level: 1, name: showName })).toBeVisible()
    await expect(page.getByText('Event created with no voting.', { exact: false })).toBeVisible()
    await expect(page.getByText(/No voting in this round \(show only\)/)).toBeVisible()
    await expect(page.getByText('Not ready to open yet')).toHaveCount(0)

    await page.goto('/admin/events')
    await expect(page.getByRole('listitem').filter({ hasText: showName })).toContainText('No voting (show only)')
  })
})
