import { expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { readInvitation } from '../lib/mailpit'
import { noHorizontalOverflow, uniqueEmail } from '../lib/participants'

test.describe('participant journey', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name === 'desktop-chromium', 'covered on phones'))

  test('invitation email, sign-in, live round, vote, receipt, next round, completion', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    await admin.ensureRunning()
    await admin.closeCurrent()
    const tag = Date.now().toString(36)

    // Invitation through the real worker and the local Mailpit inbox.
    const email = uniqueEmail('journey')
    await admin.importParticipants([`Journey Tester <${email}>`])
    const participant = await admin.findParticipant(email)
    await admin.invite(participant.id)
    const invitation = await readInvitation(email)
    // The user ID is the email address; the password arrives in the same email.
    expect(invitation.userId).toBe(email)
    expect(invitation.html).toContain(email)
    expect(invitation.html).toContain(invitation.password)
    expect(invitation.link).not.toContain(invitation.password) // never in a URL

    // No sign-up, activation or password reset: the sign-in page offers none of them.
    await page.goto(invitation.link)
    await expect(page.getByRole('link', { name: /forgot|set up|sign up|reset/i })).toHaveCount(0)
    await page.getByLabel('Email address').fill(invitation.userId)
    await page.getByLabel('Password', { exact: true }).fill(invitation.password)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/lobby$/)
    await expect(page.getByRole('heading', { name: 'The next round opens soon' })).toBeVisible()
    await noHorizontalOverflow(page)

    // The organiser opens a round: the live card appears without a reload.
    const roundA = await admin.createEvent(`Journey A ${tag}`, ['Aurora', 'Borealis', 'Cirrus'])
    const roundB = await admin.createEvent(`Journey B ${tag}`, ['Delta', 'Echo', 'Foxtrot'])
    await admin.open(roundA)
    const liveCard = page.getByRole('link', { name: /Vote now/ })
    await expect(liveCard).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(`Journey A ${tag}`).first()).toBeVisible()
    await liveCard.click()
    await expect(page).toHaveURL(new RegExp(`/event/${roundA}$`))

    // Choose, submit, and see the receipt only after the server confirmed it.
    const submit = page.getByRole('button', { name: 'Submit vote' })
    await expect(submit).toBeDisabled()
    await page.getByText('Borealis', { exact: true }).click()
    await expect(page.getByRole('radio', { name: /Borealis/ })).toBeChecked()
    await expect(submit).toBeEnabled()
    await submit.click()
    const receipt = page.getByRole('region', { name: /Receipt for/ })
    await expect(receipt).toBeVisible()
    await expect(receipt).toContainText('Vote recorded')
    await expect(receipt).toContainText('Borealis')
    const code = (await receipt.getByText(/^R-[A-Z0-9]{4}-[A-Z0-9]{4}$/).textContent())!.trim()

    // The locked state comes from the server after a reload.
    await page.reload()
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText(code)
    await expect(page.getByRole('radio')).toHaveCount(0)

    // The next round goes live while this page is open.
    await admin.open(roundB)
    const banner = page.getByText(`Next event is live: Journey B ${tag}`)
    await expect(banner).toBeVisible({ timeout: 15_000 })
    await page.getByRole('button', { name: `Go to Journey B ${tag}` }).first().click()
    await expect(page).toHaveURL(new RegExp(`/event/${roundB}$`))
    await page.getByText('Echo', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Echo')

    // Completion.
    await admin.closeCurrent()
    await admin.endProgramme()
    await page.goto('/lobby')
    await expect(page.getByRole('heading', { name: 'Thank you for voting' })).toBeVisible()
    await expect(page.getByText(code)).toBeVisible()

    // Aggregates reflect exactly these votes.
    const results = await admin.results(roundA)
    const borealis = results.events[0]!.ballots[0]!.nominees.find((n) => n.name === 'Borealis')
    expect(borealis?.votes).toBe(1)
    await admin.ensureRunning()
    await admin.dispose()
  })
})
