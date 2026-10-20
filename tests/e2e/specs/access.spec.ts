import { expect, request, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { invitedParticipant, signIn } from '../lib/participants'

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
      await page.goto('/')
      await expect(page.getByRole('region', { name: 'Time until sign-in opens' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Sign-in opens soon' })).toBeDisabled()

      // Removing the disabled attribute does not help: the API refuses early sign-in.
      const api = await request.newContext({ baseURL, extraHTTPHeaders: { Origin: baseURL! } })
      const early = await api.post('/api/v1/auth/login', { data: { user_id: person.userId, password: person.password } })
      expect(early.status()).toBe(403)
      expect((await early.json()).error.code).toBe('LOGIN_NOT_OPEN')
      await api.dispose()

      // When the countdown reaches zero, sign-in opens without a reload.
      await page.clock.resume()
      await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled({ timeout: 40_000 })
    } finally {
      await admin.patchProgramme({
        login_opens_at: new Date(now - 3600_000).toISOString(),
        login_closes_at: new Date(now + 8 * 3600_000).toISOString(),
      })
      await admin.dispose()
    }
  })

  test('one active browser session; organiser reset ends it live', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'session')
    const first = await browser.newContext()
    const second = await browser.newContext()
    const page = await first.newPage()
    await signIn(page, person.userId, person.password)

    const other = await second.newPage()
    await other.goto('/')
    await other.getByLabel('Email address').fill(person.userId)
    await other.getByLabel('Password', { exact: true }).fill(person.password)
    await other.getByRole('button', { name: 'Sign in' }).click()
    await expect(other.getByRole('alert').filter({ hasText: 'already signed in' })).toContainText('already signed in on another browser')

    // A reload in the first browser keeps the same session.
    await page.reload()
    await expect(page).toHaveURL(/\/lobby$/)

    // The help desk resets the session: the open page learns it immediately.
    await admin.resetSession(person.id)
    await expect(page.getByRole('heading', { name: 'You are signed out' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/reset by the organisers/)).toBeVisible()

    // The second browser can now sign in.
    await other.getByRole('button', { name: 'Sign in' }).click()
    await expect(other).toHaveURL(/\/lobby$/)
    await first.close()
    await second.close()
    await admin.dispose()
  })

  test('lost password: the organiser re-sends the invitation, the old password and session stop working', async ({ page, baseURL, browser }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'resend')
    const holder = await browser.newContext()
    const holderPage = await holder.newPage()
    await signIn(holderPage, person.userId, person.password)

    await admin.invite(person.id)
    // The open page learns that its session ended.
    await expect(holderPage.getByRole('heading', { name: 'You are signed out' })).toBeVisible({ timeout: 15_000 })
    await expect(holderPage.getByText(/sent you a new invitation/)).toBeVisible()

    const { waitForMessage } = await import('../lib/mailpit')
    // The newest invitation carries the new password; the old one no longer works.
    let latest = ''
    await expect
      .poll(async () => {
        const res = await fetch(`${process.env.MAILPIT_URL ?? 'http://localhost:8085'}/api/v1/search?query=${encodeURIComponent(`to:"${person.email}"`)}`)
        const data = (await res.json()) as { messages: { ID: string }[] }
        if (data.messages.length < 2) return 0
        const message = await (await fetch(`${process.env.MAILPIT_URL ?? 'http://localhost:8085'}/api/v1/message/${data.messages[0]!.ID}`)).json()
        latest = (message.Text as string).match(/Password:\s+([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})/)?.[1] ?? ''
        return data.messages.length
      }, { timeout: 45_000 })
      .toBeGreaterThanOrEqual(2)
    void waitForMessage
    expect(latest).not.toBe('')
    expect(latest).not.toBe(person.password)

    await page.goto('/')
    await page.getByLabel('Email address').fill(person.email)
    await page.getByLabel('Password', { exact: true }).fill(person.password)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'not right' })).toBeVisible()
    await signIn(page, person.email, latest)
    await holder.close()
    await admin.dispose()
  })

  test('gold look: the stage sign-in refuses a wrong password, then signs in', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const before = (await admin.programme()) as unknown as { accent: string }
    const person = await invitedParticipant(admin, baseURL!, 'stage')
    await admin.patchProgramme({ accent: 'gold' })
    try {
      await page.goto('/')
      const enter = page.getByRole('button', { name: 'Enter the arena' })
      await expect(enter).toBeEnabled()
      await page.getByLabel('Email ID').fill(person.userId)
      await page.getByLabel('Password', { exact: true }).fill('WRONG-PASS-0000')
      await enter.click()
      await expect(page.getByRole('alert').filter({ hasText: 'not right' })).toContainText('That user ID or password is not right.')
      await page.getByLabel('Password', { exact: true }).fill(person.password)
      await enter.click()
      await expect(page).toHaveURL(/\/lobby$/)
    } finally {
      await admin.patchProgramme({ accent: before.accent })
      await admin.dispose()
    }
  })
})
