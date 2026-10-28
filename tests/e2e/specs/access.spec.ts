import { type Page, expect, request, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { readInvitation } from '../lib/mailpit'
import { entryButton, invitedParticipant, linkToken, openLink, signIn } from '../lib/participants'

/** Every request a page makes from now on (address and body), to check what left the phone. */
function recordRequests(page: Page) {
  const seen: { url: string; body: string }[] = []
  page.on('request', (r) => seen.push({ url: r.url(), body: r.postData() ?? '' }))
  return seen
}

test.describe('access window and sessions', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'mobile-chromium', 'server rules; one browser is enough'))

  test('countdown and server-enforced sign-in window, even with a wrong device clock', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'window')
    const now = Date.now()
    await admin.patchProgramme({
      login_opens_at: new Date(now + 20_000).toISOString(),
      login_closes_at: new Date(now + 6 * 3600_000).toISOString(),
      countdown_enabled: true,
    })
    try {
      // The phone's clock claims tomorrow; the page still follows the server's time.
      await page.clock.install({ time: new Date(now + 24 * 3600_000) })
      await openLink(page, person.link)
      await expect(page.getByRole('region', { name: 'Time until sign-in opens' })).toBeVisible()
      await expect(page.getByRole('button', { name: /opens soon/i })).toBeDisabled()

      // Removing the disabled attribute does not help: the API refuses an early tap, and the
      // refused tap does not tie the link to the browser that sent it.
      const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL! } })
      const early = await api.post('/api/v1/auth/link', { data: { token: linkToken(person.link) } })
      expect(early.status()).toBe(403)
      expect((await early.json()).error.code).toBe('LOGIN_NOT_OPEN')
      await api.dispose()
      expect((await admin.participant(person.id)).participant.link_bound_at).toBeNull()

      // When the countdown reaches zero, the button opens without a reload and the link
      // enters on this phone.
      await page.clock.resume()
      const enter = entryButton(page)
      await expect(enter).toBeEnabled({ timeout: 40_000 })
      await enter.click()
      await expect(page).toHaveURL(/\/lobby$/)
    } finally {
      await admin.patchProgramme({
        login_opens_at: new Date(now - 3600_000).toISOString(),
        login_closes_at: new Date(now + 8 * 3600_000).toISOString(),
      })
      await admin.dispose()
    }
  })

  test('one device per link: opening it binds nothing, the first tap keeps it, copies are refused', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'device')
    const token = linkToken(person.link)
    const forwarded = await browser.newContext()
    const phone = await browser.newContext()
    const forwardedPage = await forwarded.newPage()
    const phonePage = await phone.newPage()
    const forwardedRequests = recordRequests(forwardedPage)
    const phoneRequests = recordRequests(phonePage)

    // A copy of the link is opened first, but nobody taps: the page only checks the link,
    // and nothing is tied to that browser.
    await openLink(forwardedPage, person.link)
    await expect(entryButton(forwardedPage)).toBeEnabled()
    await expect(forwardedPage).toHaveURL(/\/enter$/) // the token left the address bar
    expect((await admin.participant(person.id)).participant.link_bound_at).toBeNull()

    // The voter's phone taps first and keeps the link.
    await signIn(phonePage, person.link)
    expect((await admin.participant(person.id)).participant.link_bound_at).not.toBeNull()

    // The copy is refused when it taps, and so is every later opening of it.
    await entryButton(forwardedPage).click()
    await expect(forwardedPage.getByText(/already in use/).first()).toBeVisible()
    await openLink(forwardedPage, person.link)
    await expect(forwardedPage.getByText(/already in use/).first()).toBeVisible()
    await expect(entryButton(forwardedPage)).toHaveCount(0)
    const session = await (await forwarded.request.get(`${baseURL}/api/v1/auth/session`)).json()
    expect(session.signed_in).toBe(false)

    // The token never appeared in an address a server could see (browsers keep the #fragment
    // to themselves): it travelled only in the bodies of the link check and the tap.
    for (const r of [...forwardedRequests, ...phoneRequests]) expect(r.url.split('#')[0]).not.toContain(token)
    const carried = phoneRequests.filter((r) => r.body.includes(token)).map((r) => new URL(r.url).pathname)
    expect(carried).toEqual(expect.arrayContaining(['/api/v1/auth/link/check', '/api/v1/auth/link']))

    // Signing out does not give the link away: the same browser comes back with it.
    await phonePage.getByRole('button', { name: /^Account menu for/ }).click()
    await phonePage.getByRole('menuitem', { name: /Sign out/ }).click()
    await expect(phonePage).toHaveURL(/\/\?notice=signed_out$/)
    await expect(phonePage.getByText(/You signed out/).first()).toBeVisible()
    await signIn(phonePage, person.link)

    await forwarded.close()
    await phone.close()
    await admin.dispose()
  })

  test('organiser sign-out everywhere ends the session live; the same phone comes back with its link', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'session')
    const phone = await browser.newContext()
    const page = await phone.newPage()
    await signIn(page, person.link)

    // A reload keeps the session.
    await page.reload()
    await expect(page).toHaveURL(/\/lobby$/)

    // The help desk signs the voter out everywhere: the open page learns it immediately.
    await admin.resetSession(person.id)
    await expect(page.getByRole('heading', { name: 'You are signed out' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/organisers signed you out/)).toBeVisible()

    // Only a new invitation releases the link, so this phone enters again with it, and a copy
    // on another browser is still refused.
    await signIn(page, person.link)
    const other = await browser.newContext()
    const otherPage = await other.newPage()
    await openLink(otherPage, person.link)
    await expect(otherPage.getByText(/already in use/).first()).toBeVisible()
    await expect(entryButton(otherPage)).toHaveCount(0)

    await other.close()
    await phone.close()
    await admin.dispose()
  })

  test('lost link: the organiser re-sends the invitation, the old link and its session stop working', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'resend')
    const holder = await browser.newContext()
    const holderPage = await holder.newPage()
    await signIn(holderPage, person.link)

    await admin.invite(person.id)
    // The open page learns that its session ended.
    await expect(holderPage.getByRole('heading', { name: 'You are signed out' })).toBeVisible({ timeout: 15_000 })
    await expect(holderPage.getByText(/sent you a new invitation/)).toBeVisible()

    // The newest invitation carries a new link. The old one no longer works, not even on the
    // phone that used it.
    const latest = await readInvitation(person.email, { count: 2 })
    expect(latest.link).not.toBe(person.link)
    await openLink(holderPage, person.link)
    await expect(holderPage.getByText(/not valid any more/).first()).toBeVisible()
    await expect(entryButton(holderPage)).toHaveCount(0)

    // The new link works on a new phone: sending it released the old one.
    const fresh = await browser.newContext()
    await signIn(await fresh.newPage(), latest.link)

    await fresh.close()
    await holder.close()
    await admin.dispose()
  })

  test('gold look: the stage has nothing to type, only Enter the arena; an example link signs nobody in', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const before = (await admin.programme()) as unknown as { accent: string }
    const person = await invitedParticipant(admin, baseURL!, 'stage')
    await admin.patchProgramme({ accent: 'gold' })
    try {
      const fields = page.locator('input:not([type="hidden"]), textarea, select')

      // Without a link, the stage only says where to find one.
      await page.goto('/')
      await expect(page.getByText(/open your invitation email/i).first()).toBeVisible()
      await expect(entryButton(page)).toHaveCount(0)
      await expect(fields).toHaveCount(0)

      // Preview and test emails carry an example link, which is refused with its own message.
      await openLink(page, `${baseURL}/enter#token=example`)
      await expect(page.getByText('This is an example link from a preview email. It does not sign anyone in.')).toBeVisible()
      await expect(entryButton(page)).toHaveCount(0)

      // The real link: one button, nothing to fill in.
      await openLink(page, person.link)
      const enter = page.getByRole('button', { name: 'Enter the arena' })
      await expect(enter).toBeEnabled()
      await expect(fields).toHaveCount(0)
      await enter.click()
      await expect(page).toHaveURL(/\/lobby$/)
    } finally {
      await admin.patchProgramme({ accent: before.accent })
      await admin.dispose()
    }
  })
})
