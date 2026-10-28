import { expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { invitedParticipant, signIn } from '../lib/participants'

test.describe('live changes and unreliable networks', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name === 'desktop-chromium', 'phone behaviour'))

  test('committed vote whose response is lost is reconciled, never duplicated', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'lost')
    const round = await admin.createEvent(`Lost response ${Date.now().toString(36)}`, ['Juniper', 'Kestrel', 'Larch'])
    await admin.open(round)
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)

    const keys: string[] = []
    let first = true
    await page.route('**/api/v1/me/ballots/*/vote', async (route) => {
      keys.push(route.request().headers()['idempotency-key'] ?? '')
      if (first) {
        first = false
        await route.fetch() // the server commits the vote ...
        await route.abort('connectionreset') // ... but the phone never sees the answer
        return
      }
      await route.continue()
    })
    // Slow the receipt check so the "checking" state is observable.
    await page.route('**/api/v1/me/ballots/*/receipt', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      await route.continue()
    })

    await page.getByText('Kestrel', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('button', { name: 'Checking your vote…' })).toBeVisible()
    await expect(page.getByRole('radio', { name: /Juniper/ })).toBeDisabled() // no other choice offered meanwhile
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Kestrel')

    const results = await admin.results(round)
    expect(results.events[0]!.ballots[0]!.total_votes).toBe(1)
    expect(new Set(keys).size).toBe(1)
    await admin.dispose()
  })

  test('request that never reached the server is retried with the same key', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'neverarrived')
    const round = await admin.createEvent(`Never arrived ${Date.now().toString(36)}`, ['Maple', 'Nutmeg'])
    await admin.open(round)
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)
    const keys: string[] = []
    let attempt = 0
    await page.route('**/api/v1/me/ballots/*/vote', async (route) => {
      keys.push(route.request().headers()['idempotency-key'] ?? '')
      attempt += 1
      if (attempt === 1) return route.abort('internetdisconnected')
      return route.continue()
    })
    await page.getByText('Nutmeg', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Nutmeg')
    expect(keys.length).toBeGreaterThanOrEqual(2)
    expect(new Set(keys).size).toBe(1)
    const results = await admin.results(round)
    expect(results.events[0]!.ballots[0]!.total_votes).toBe(1)
    await admin.dispose()
  })

  test('offline: voting pauses, the draft is kept, and resumes after reconnecting', async ({ page, context, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'offline')
    const round = await admin.createEvent(`Offline ${Date.now().toString(36)}`, ['Oak', 'Pine'])
    await admin.open(round)
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)
    await page.getByText('Pine', { exact: true }).click()

    await context.setOffline(true)
    await expect(page.getByRole('status').filter({ hasText: 'You are offline' })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByRole('button', { name: 'Waiting for connection' })).toBeDisabled()
    await expect(page.getByRole('radio', { name: /Pine/ })).toBeChecked()

    await context.setOffline(false)
    const submit = page.getByRole('button', { name: 'Submit vote' })
    await expect(submit).toBeEnabled({ timeout: 20_000 })
    await submit.click()
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Pine')
    await admin.dispose()
  })

  test('round closes while the ballot is open on screen: nothing is submitted', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'closing')
    const round = await admin.createEvent(`Closing ${Date.now().toString(36)}`, ['Quince', 'Rowan'])
    await admin.open(round)
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)
    await page.getByText('Rowan', { exact: true }).click()
    await admin.closeCurrent()
    await expect(page.getByRole('alert').filter({ hasText: 'Voting for this round has closed' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: 'Submit vote' })).toHaveCount(0)
    const results = await admin.results(round)
    expect(results.events[0]!.ballots[0]!.total_votes).toBe(0)
    await admin.dispose()
  })

  test('next round opens while the previous ballot is visible: no silent transfer', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'nextlive')
    const stamp = Date.now().toString(36)
    const first = await admin.createEvent(`Earlier ${stamp}`, ['Sage', 'Thyme'])
    const second = await admin.createEvent(`Later ${stamp}`, ['Umber', 'Violet'])
    await admin.open(first)
    await signIn(page, person.link)
    await page.goto(`/event/${first}`)
    await page.getByText('Thyme', { exact: true }).click()
    await admin.open(second)
    await expect(page.getByText(`Next event is live: Later ${stamp}`)).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('Any choice you have not submitted here was not sent.')).toBeVisible()
    await page.getByRole('button', { name: `Go to Later ${stamp}` }).first().click()
    await expect(page).toHaveURL(new RegExp(`/event/${second}$`))
    await expect(page.getByRole('radio', { name: /Umber/ })).not.toBeChecked()
    await expect(page.getByRole('radio', { name: /Violet/ })).not.toBeChecked()
    const results = await admin.results(first)
    expect(results.events[0]!.ballots[0]!.total_votes).toBe(0)
    await admin.dispose()
  })

  test('lobby reveals a newly opened round without a reload, and multi-ballot events keep separate receipts', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'multi')
    await admin.closeCurrent()
    await signIn(page, person.link)
    const round = await admin.createEvent(`Two ballots ${Date.now().toString(36)}`, ['Wren', 'Yarrow'], 'team', 2)
    await admin.open(round)
    await expect(page.getByRole('link', { name: /Vote now/ })).toBeVisible({ timeout: 15_000 })
    await page.getByRole('link', { name: /Vote now/ }).click()
    await expect(page.getByRole('heading', { name: 'Best team' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Best member' })).toBeVisible()
    const teams = page.getByRole('radiogroup', { name: 'Best team' })
    await teams.getByText('Wren', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('region', { name: 'Receipt for Best team' })).toBeVisible()
    // One ballot is still open, so the page stays for it instead of going back.
    await expect(page.getByText('Going back to the programme…')).toHaveCount(0)
    const members = page.getByRole('radiogroup', { name: 'Best member' })
    await members.getByText('Yarrow', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('region', { name: 'Receipt for Best member' })).toBeVisible()
    // Now the round is complete; "Stay on this page" stops the automatic return.
    await expect(page.getByText('Going back to the programme…')).toBeVisible()
    await page.getByRole('button', { name: 'Stay on this page' }).click()
    await page.waitForTimeout(4_500)
    await expect(page).toHaveURL(new RegExp(`/event/${round}$`))
    await admin.dispose()
  })
})
