import { randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { type Page, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { waitForMessage } from '../lib/mailpit'
import { noHorizontalOverflow, uniqueEmail } from '../lib/participants'

/**
 * The photo gallery (/gallery), through the real OpenCV models in the stack: the portraits are two
 * framings of one public-domain face (apps/api/tests/fixtures/README.md). No vision model runs here.
 *
 * Each upload gets a little noise at its end, after the picture data, because the gallery counts
 * the same bytes only once and the test database lives on between runs. The face is the same in every
 * test, and a selfie that matches someone joins them under their name, so each test starts with nobody
 * in the gallery, even after a run that failed halfway.
 */

test.beforeEach(async ({ baseURL }) => {
  const admin = await Admin.login(baseURL!)
  await admin.clearGallery()
  await admin.dispose()
})

const FIXTURES = join(import.meta.dirname, '../../../apps/api/tests/fixtures')

function portrait(file: 'portrait-selfie.jpg' | 'portrait-wide.jpg') {
  return {
    name: file,
    mimeType: 'image/jpeg',
    buffer: Buffer.concat([readFileSync(join(FIXTURES, file)), randomBytes(16)]),
  }
}

async function fillForm(page: Page, name: string) {
  await page.getByLabel('Upload a photo of yourself').setInputFiles(portrait('portrait-selfie.jpg'))
  await page.getByLabel('Your name').fill(name)
  await page.getByRole('checkbox').check()
}

const uploadHeading = (page: Page) => page.getByRole('heading', { name: 'Share your photos and videos' })

async function seriousViolations(page: Page, within?: string) {
  const builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
  const scan = await (within ? builder.include(within) : builder).analyze()
  return scan.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}

async function removeMe(page: Page) {
  await page.locator('.gallery-me').click()
  await page.getByRole('button', { name: 'Remove me from the gallery' }).click()
  await page.getByRole('button', { name: 'Remove me', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Find yourself in every photo' })).toBeVisible()
}

test('a selfie and a name, then a shared photo shows on your page, untouched', async ({ page }, info) => {
  const name = `Gallery ${info.project.name} ${Date.now().toString(36)}`
  await page.goto('/gallery')
  await expect(page.getByRole('heading', { name: 'Find yourself in every photo' })).toBeVisible()
  await noHorizontalOverflow(page)
  expect(await seriousViolations(page)).toEqual([])

  // The form says what is missing, and nothing is sent.
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('Take a selfie or upload a photo of yourself.')).toBeVisible()
  await expect(page.getByText('Enter your name.')).toBeVisible()
  await expect(page.getByText('Tick the box to agree')).toBeVisible()

  await fillForm(page, name)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(uploadHeading(page)).toBeVisible({ timeout: 30_000 })
  await noHorizontalOverflow(page)
  expect(await seriousViolations(page)).toEqual([])

  // The form does not come back.
  await page.reload()
  await expect(uploadHeading(page)).toBeVisible()

  // The person only hears "uploaded".
  const shared = portrait('portrait-wide.jpg')
  await page.getByLabel('Choose photos and videos').setInputFiles(shared)
  await expect(page.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Uploaded', { exact: true })).toBeVisible()
  expect(await seriousViolations(page)).toEqual([])

  // Searching by name finds the person, with the photo once it is sorted.
  await page.getByRole('button', { name: 'Open People' }).click()
  await page.getByRole('searchbox').fill(name)
  const card = page.getByRole('button', { name: /^You/ })
  await expect(card).toBeVisible()
  await expect(async () => {
    await page.reload()
    await page.getByRole('searchbox').fill(name)
    await expect(card).toContainText('1 photo', { timeout: 3000 })
  }).toPass({ timeout: 60_000 })
  await noHorizontalOverflow(page)
  expect(await seriousViolations(page)).toEqual([])

  await card.click()
  await expect(page.getByRole('heading', { name })).toBeVisible()
  await expect(page.getByText('1 photo', { exact: true })).toBeVisible()
  await noHorizontalOverflow(page)
  expect(await seriousViolations(page)).toEqual([])

  // The viewer shows the file as it was sent, and the back button closes it.
  await page.locator('.gallery-photo button').first().click()
  const picture = page.locator('.gallery-viewer img')
  await expect(picture).toBeVisible()
  const source = await picture.getAttribute('src')
  const served = await page.request.get(source!)
  expect(served.ok()).toBe(true)
  expect(served.headers()['content-type']).toBe('image/jpeg')
  expect(Buffer.compare(await served.body(), shared.buffer)).toBe(0)
  expect(await page.getByRole('link', { name: 'Download the full-quality file' }).getAttribute('href')).toBe(source)
  await page.goBack()
  await expect(picture).toBeHidden()
  await expect(page.getByRole('heading', { name })).toBeVisible()

  await removeMe(page)
})

test('a picture with no face in it is refused, in words', async ({ page }) => {
  await page.goto('/gallery')
  const blank = readFileSync(join(import.meta.dirname, '../../../apps/web/public/brand/techsara-mark.png'))
  await page.getByLabel('Upload a photo of yourself').setInputFiles({ name: 'mark.png', mimeType: 'image/png', buffer: blank })
  await page.getByLabel('Your name').fill('No Face')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('We could not find a face in that photo.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Find yourself in every photo' })).toBeVisible()
})

test('only the gallery may ask for the camera, and media stays plain', async ({ request }) => {
  const gallery = await request.get('/gallery')
  expect(gallery.headers()['permissions-policy']).toContain('camera=(self)')
  const lobby = await request.get('/')
  expect(lobby.headers()['permissions-policy']).toContain('camera=()')
  // A file still waiting for the worker can never be fetched.
  const waiting = await request.get('/media/.gallery-incoming/0123456789abcdef0123456789abcdef.jpg')
  expect(waiting.status()).toBe(404)
})

test('the gallery email and its preview go to addresses typed with commas', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop-chromium', 'admin flows run on desktop')
  const previews = [uniqueEmail('preview-one'), uniqueEmail('preview-two')]
  const people = [uniqueEmail('list-one'), uniqueEmail('list-two')]
  await page.goto('/admin/login')
  await page.getByLabel('Email').fill(process.env.ADMIN_EMAIL!)
  await page.getByLabel('Password', { exact: true }).fill(process.env.ADMIN_PASSWORD!)
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForURL((url) => !url.pathname.includes('login'))
  await page.goto('/admin/gallery')

  await page.getByLabel('Send a preview to').fill(previews.join(', '))
  await page.getByRole('button', { name: 'Send a preview', exact: true }).click()
  for (const email of previews) await waitForMessage(email, '[Preview]')

  await page.getByLabel('Send the gallery email to').fill(people.join(', '))
  await page.getByRole('button', { name: 'Send the email', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Send the email' }).click()
  await expect(page.getByText('The gallery email is on its way')).toBeVisible()
  for (const email of people) {
    const message = await waitForMessage(email, 'share your photos')
    expect(message.text).toContain('/gallery#token=')
    expect(message.text).not.toContain('/enter#token=')
  }
})

test('the email link fills in the name, follows to a second device and leaves voting alone', async ({ browser, baseURL }) => {
  const admin = await Admin.login(baseURL!)
  const email = uniqueEmail('gallery')
  await admin.importParticipants([`Gallery Guest <${email}>`])
  await admin.sendGalleryEmail()
  const message = await waitForMessage(email, 'share your photos')
  const link = message.text.match(/Open the gallery:\s+(https?:\/\/\S+)/)?.[1]
  expect(link, message.text).toContain('/gallery#token=')
  expect(message.text).not.toContain('/enter#token=')

  const first = await browser.newContext({ baseURL })
  const page = await first.newPage()
  await page.goto(link!)
  await expect(page.getByLabel('Your name')).toHaveValue('Gallery Guest')
  await expect(page.getByText('Hi Gallery Guest.')).toBeVisible()
  await page.getByLabel('Upload a photo of yourself').setInputFiles(portrait('portrait-selfie.jpg'))
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(uploadHeading(page)).toBeVisible({ timeout: 30_000 })
  expect(page.url()).not.toContain('token') // the link's token leaves the address

  const second = await browser.newContext({ baseURL })
  const phone = await second.newPage()
  await phone.goto(link!)
  await expect(uploadHeading(phone)).toBeVisible({ timeout: 30_000 })
  await second.close()

  await removeMe(page)
  await first.close()
})

test('organisers see what the analysis saw, and visitors never do', async ({ page, browser, baseURL }, info) => {
  const unique = `${info.project.name}-${Date.now().toString(36)}`
  const name = `Analysis ${unique}`
  const file = `analysis-${unique}.jpg`
  const visitor = await browser.newContext({ baseURL })
  const guest = await visitor.newPage()
  await guest.goto('/gallery')
  await fillForm(guest, name)
  await guest.getByRole('button', { name: 'Continue' }).click()
  await expect(uploadHeading(guest)).toBeVisible({ timeout: 30_000 })
  await guest.getByLabel('Choose photos and videos').setInputFiles({ ...portrait('portrait-wide.jpg'), name: file })
  await expect(guest.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })

  await page.goto('/admin/login')
  await page.getByLabel('Email').fill(process.env.ADMIN_EMAIL!)
  await page.getByLabel('Password', { exact: true }).fill(process.env.ADMIN_PASSWORD!)
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForURL((url) => !url.pathname.includes('login'))
  const card = page.locator('li', { hasText: file })
  await expect(async () => {
    await page.goto('/admin/gallery')
    await expect(card.getByRole('button', { name: /See analysis/ })).toBeVisible({ timeout: 3000 })
  }).toPass({ timeout: 60_000 })

  await card.getByRole('button', { name: /See analysis/ }).click()
  const dialog = page.getByRole('dialog', { name: `Analysis of ${file}` })
  await expect(dialog.getByText('Found 1 face in this photo.')).toBeVisible()
  await expect(dialog.getByText('No vision model is set')).toBeVisible() // none in this test stack
  await expect(dialog.locator('li button', { hasText: name })).toContainText(/OpenCV 0\.\d+: clear match/)
  await noHorizontalOverflow(page)
  expect(await seriousViolations(page, 'dialog[open]')).toEqual([])

  // The box sits inside the picture, around a real part of it, and can be hidden.
  const box = dialog.locator('[data-analysis-box]')
  await expect(box).toHaveCount(1)
  const picture = (await dialog.locator('img').first().boundingBox())!
  const area = (await box.boundingBox())!
  expect(area.x).toBeGreaterThanOrEqual(picture.x)
  expect(area.y).toBeGreaterThanOrEqual(picture.y)
  expect(area.x + area.width).toBeLessThanOrEqual(picture.x + picture.width + 1)
  expect(area.y + area.height).toBeLessThanOrEqual(picture.y + picture.height + 1)
  expect(area.width).toBeGreaterThan(10)
  await dialog.getByRole('checkbox', { name: 'Show boxes' }).uncheck()
  await expect(box).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(dialog).toBeHidden()

  // The visitor's page shows the same file with no marks, and the API tells them nothing about it.
  await guest.reload()
  await guest.getByRole('button', { name: 'People', exact: true }).click()
  await guest.getByRole('searchbox').fill(name)
  await guest.getByRole('button', { name: /^You/ }).click()
  await guest.locator('.gallery-photo button').first().click()
  await expect(guest.locator('.gallery-viewer img')).toBeVisible()
  await expect(guest.locator('[data-analysis-box]')).toHaveCount(0)
  const page_ = await guest.request.get(`/api/v1/gallery/people?q=${encodeURIComponent(name)}`)
  expect(await page_.text()).not.toContain('box')
  await guest.goBack()
  await removeMe(guest)
  await visitor.close()

  // Cleaning up: the file goes away with its analysis.
  await card.getByRole('button', { name: /^Remove/ }).click()
  await page.getByRole('button', { name: 'Remove the file' }).click()
  await expect(card).toHaveCount(0)
})

test.describe('layout', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'mobile-chromium', 'the width sweep runs once'))

  test('nothing overflows from 320 pixels up', async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, hasTouch: true, isMobile: true })
    const page = await context.newPage()
    await page.goto('/gallery')
    await fillForm(page, `A very long name that keeps on going ${Date.now().toString(36)}`)
    for (const width of [320, 375, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 800 })
      await noHorizontalOverflow(page)
    }
    await page.setViewportSize({ width: 390, height: 800 })
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(uploadHeading(page)).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'People', exact: true }).click()
    await expect(page.getByRole('searchbox')).toBeVisible()
    for (const width of [320, 375, 430, 768, 1440]) {
      await page.setViewportSize({ width, height: 800 })
      await noHorizontalOverflow(page)
    }
    // On a phone the tabs sit at the foot of the screen, and your coin stays reachable.
    await page.setViewportSize({ width: 390, height: 800 })
    const tabs = await page.locator('.gallery-tabs').boundingBox()
    expect(tabs!.y + tabs!.height).toBeCloseTo(800, -1)
    await expect(page.locator('.gallery-me')).toBeVisible()
    await removeMe(page)
    await context.close()
  })
})
