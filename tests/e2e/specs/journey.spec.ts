import { expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { readInvitation } from '../lib/mailpit'
import { entryButton, noHorizontalOverflow, openLink, uniqueEmail } from '../lib/participants'

test.describe('participant journey', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name === 'desktop-chromium', 'covered on phones'))

  test('invitation email, sign-in, live round, vote, receipt, next round, completion', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    await admin.ensureRunning()
    await admin.ensureAccessOpen()
    await admin.closeCurrent()
    const tag = Date.now().toString(36)

    // Invitation through the real worker and the local Mailpit inbox.
    const email = uniqueEmail('journey')
    await admin.importParticipants([`Journey Tester <${email}>`])
    const participant = await admin.findParticipant(email)
    await admin.invite(participant.id)
    const invitation = await readInvitation(email)
    // The email carries a personal link behind its button, and no user ID or password. The
    // token rides in the #fragment, which browsers never send to a server.
    const link = new URL(invitation.link)
    expect(`${link.origin}${link.pathname}`).toBe(`${baseURL}/enter`)
    expect(link.search).toBe('')
    expect(link.hash).toBe(`#token=${invitation.token}`)
    expect(invitation.html).toContain(invitation.link)
    expect(invitation.text).not.toContain('User ID')
    expect(invitation.text).not.toContain('Password:')

    // No sign-up, activation or password reset, and nothing to type: one button enters.
    await openLink(page, invitation.link)
    const enter = entryButton(page)
    await expect(enter).toBeEnabled()
    await expect(page).toHaveURL(/\/enter$/) // the token left the address bar
    await expect(page.getByRole('link', { name: /forgot|set up|sign up|reset/i })).toHaveCount(0)
    await expect(page.locator('input:not([type="hidden"])')).toHaveCount(0)
    await enter.click()
    await expect(page).toHaveURL(/\/lobby$/)
    await expect(page.getByRole('heading', { name: 'The next round opens soon' })).toBeVisible()
    await noHorizontalOverflow(page)

    // The organiser's welcome replaces the built-in text without a reload; empty brings it back.
    await admin.patchProgramme({ welcome_title: `Welcome to Dhanvarsha ${tag}`, welcome_message: 'Find a seat.\nRound one starts soon.' })
    try {
      await expect(page.getByRole('heading', { name: `Welcome to Dhanvarsha ${tag}` })).toBeVisible({ timeout: 15_000 })
      await expect(page.getByText('Round one starts soon.')).toBeVisible()
      // The welcome shows alone: no "Next: Round …" line under it.
      await expect(page.getByText(/^Next: Round/)).toHaveCount(0)
      await noHorizontalOverflow(page)
    } finally {
      await admin.patchProgramme({ welcome_title: '', welcome_message: '' })
    }
    await expect(page.getByRole('heading', { name: 'The next round opens soon' })).toBeVisible({ timeout: 15_000 })

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
    // The round is fully voted, so the page shows the receipt for a few seconds and then goes
    // back to the programme by itself.
    await expect(page.getByText('Going back to the programme…')).toBeVisible()
    await expect(page).toHaveURL(/\/lobby$/, { timeout: 8_000 })

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
