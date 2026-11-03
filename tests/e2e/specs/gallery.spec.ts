import { randomBytes } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { type Browser, type Page, expect, test } from '@playwright/test'
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

async function signInAsOrganiser(page: Page) {
  await page.goto('/admin/login')
  await page.getByLabel('Email').fill(process.env.ADMIN_EMAIL!)
  await page.getByLabel('Password', { exact: true }).fill(process.env.ADMIN_PASSWORD!)
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForURL((url) => !url.pathname.includes('login'))
}

const uploadPanel = (page: Page) => page.getByRole('region', { name: 'Upload photos and videos' })
const UPLOAD_PANEL = 'section[aria-labelledby="gallery-upload"]'

/** No bucket in this stack: the page is told there is one, as a page loaded before S3 was turned off was. */
async function consoleThinksS3IsOn(page: Page) {
  await page.route('**/api/v1/admin/gallery', async (route) => {
    const response = await route.fetch()
    const summary = await response.json()
    await route.fulfill({ response, json: { ...summary, storage: { ...summary.storage, where: 's3', bucket: 'test-bucket' } } })
  })
}

/** Someone with a selfie and a name, in a browser of their own. */
async function enrolledGuest(browser: Browser, baseURL: string, name: string) {
  const visitor = await browser.newContext({ baseURL })
  const guest = await visitor.newPage()
  await guest.goto('/gallery')
  await fillForm(guest, name)
  await guest.getByRole('button', { name: 'Continue' }).click()
  await expect(uploadHeading(guest)).toBeVisible({ timeout: 30_000 })
  return { visitor, guest }
}

/** Drops real files and folders on the panel. Playwright cannot drag them, but Chromium's protocol can. */
async function dropOnPanel(page: Page, files: string[]) {
  const panel = uploadPanel(page)
  const client = await page.context().newCDPSession(page)
  const data = { items: [], files, dragOperationsMask: 1 }
  const drag = async (type: 'dragEnter' | 'dragOver' | 'drop') => {
    const box = (await panel.getByRole('button', { name: 'Choose a folder' }).boundingBox())!
    await client.send('Input.dispatchDragEvent', { type, x: box.x + box.width / 2, y: box.y + box.height / 2, data })
  }
  await expect(page.getByTitle('Gallery email preview')).toBeVisible() // it loads late, and moves the panel
  await panel.getByRole('button', { name: 'Choose a folder' }).scrollIntoViewIfNeeded()
  await drag('dragEnter')
  await drag('dragOver')
  await expect(panel.getByText('Let go to add them')).toBeVisible()
  await drag('drop')
  await client.detach()
}

/** A few seconds of the portrait as a real video, recorded in a browser: no fixture is a video. */
async function portraitVideo(browser: Browser): Promise<Buffer> {
  const page = await browser.newPage()
  const recorded = await page.evaluate(async (picture) => {
    const image = await createImageBitmap(await (await fetch(`data:image/jpeg;base64,${picture}`)).blob())
    const canvas = Object.assign(document.createElement('canvas'), { width: image.width, height: image.height })
    const paint = canvas.getContext('2d')!
    const recorder = new MediaRecorder(canvas.captureStream(10), { mimeType: 'video/webm' })
    const parts: Blob[] = []
    recorder.ondataavailable = (event) => parts.push(event.data)
    const stopped = new Promise((resolve) => (recorder.onstop = resolve))
    recorder.start()
    for (let frame = 0; frame < 30; frame += 1) {
      paint.drawImage(image, 0, 0)
      paint.fillStyle = `rgb(${frame} 0 0)` // a stream sends nothing new while the picture stands still
      paint.fillRect(0, 0, 4, 4)
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
    recorder.stop()
    await stopped
    const bytes = new Uint8Array(await new Blob(parts).arrayBuffer())
    return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''))
  }, readFileSync(join(FIXTURES, 'portrait-wide.jpg')).toString('base64'))
  await page.close()
  return Buffer.from(recorded, 'base64')
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

  await expect(card.getByText('Sent from the console')).toHaveCount(0)
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

test.describe('organisers upload from the console', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-chromium', 'admin flows run on desktop'))

  test('a photo from the console lands on the page of the person in it', async ({ page, browser, baseURL }) => {
    const unique = Date.now().toString(36)
    const name = `Organiser photo ${unique}`
    const file = `organiser-${unique}.jpg`
    // Over 16 MiB of noise: several whole chunks, which only pass the proxy and the API as chunks of a known route.
    const fixture = portrait('portrait-wide.jpg')
    const uploaded = { ...fixture, name: file, buffer: Buffer.concat([fixture.buffer, randomBytes(17 * 1024 * 1024)]) }
    const { visitor, guest } = await enrolledGuest(browser, baseURL!, name)

    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const people = page.getByRole('region', { name: 'People', exact: true })
    await people.getByRole('searchbox').fill(name)
    await expect(people.getByText('No photos yet')).toBeVisible()

    await uploadPanel(page).getByLabel('Choose photos and videos').setInputFiles(uploaded)
    await expect(uploadPanel(page).getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })

    // Nobody reloads the page: the file and the person's count arrive by themselves, and the search stays.
    const card = page.getByRole('region', { name: 'Files', exact: true }).locator('li', { hasText: file })
    await expect(card).toContainText('Organiser, 1 face', { timeout: 60_000 })
    await expect(card.getByText('Sent from the console')).toBeVisible()
    await expect(people.getByText('1 photo', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(people.getByRole('searchbox')).toHaveValue(name)
    await expect(uploadPanel(page).getByText('still being sorted')).toBeHidden()

    // Once nothing is moving, the page looks once more and stops asking.
    await expect(page.locator('dt:has-text("Waiting to be sorted") + dd')).toHaveText('0')
    await page.waitForTimeout(1500)
    let asked = 0
    page.on('request', (request) => {
      if (request.url().includes('/api/v1/admin/gallery')) asked += 1
    })
    await page.waitForTimeout(6000)
    expect(asked).toBe(0)

    // The person's own page has it, as it was sent.
    await guest.reload()
    await guest.getByRole('button', { name: 'People', exact: true }).click()
    await guest.getByRole('searchbox').fill(name)
    const mine = guest.getByRole('button', { name: /^You/ })
    await expect(mine).toContainText('1 photo')
    await mine.click()
    await expect(guest.getByRole('heading', { name })).toBeVisible()
    await guest.locator('.gallery-photo button').first().click()
    const picture = guest.locator('.gallery-viewer img')
    await expect(picture).toBeVisible()
    const served = await guest.request.get((await picture.getAttribute('src'))!)
    expect(Buffer.compare(await served.body(), uploaded.buffer)).toBe(0)
    await guest.goBack()
    await removeMe(guest)
    await visitor.close()

    await page.goto('/admin/audit')
    await expect(page.getByText('Uploaded a gallery file').first()).toBeVisible()
  })

  test('the panel fits a phone and reads well', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.setViewportSize({ width: 320, height: 800 })
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    await expect(panel.getByText('Drop photos, videos or whole folders here')).toBeVisible()
    await noHorizontalOverflow(page)
    expect(await seriousViolations(page, UPLOAD_PANEL)).toEqual([])

    // A row with a very long name, a row that was refused, and the words around them.
    const long = `${'a-very-long-file-name-that-keeps-on-going-'.repeat(4)}.jpg`
    await panel.getByLabel('Choose photos and videos').setInputFiles([
      { ...portrait('portrait-wide.jpg'), name: long },
      { name: 'not-a-picture.jpg', mimeType: 'image/jpeg', buffer: randomBytes(64) },
    ])
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    await expect(panel.getByText('1 file could not be sent.')).toBeVisible()
    await noHorizontalOverflow(page)
    const box = (await panel.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(320)
    expect(await panel.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(0)
    expect(await seriousViolations(page, UPLOAD_PANEL)).toEqual([])
  })

  test('a video from the console does the same', async ({ page, browser, baseURL }) => {
    const unique = Date.now().toString(36)
    const name = `Organiser video ${unique}`
    const file = `organiser-${unique}.webm`
    const { visitor, guest } = await enrolledGuest(browser, baseURL!, name)

    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    await uploadPanel(page).getByLabel('Choose photos and videos').setInputFiles({ name: file, mimeType: 'video/webm', buffer: await portraitVideo(browser) })
    await expect(uploadPanel(page).getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    const card = page.getByRole('region', { name: 'Files', exact: true }).locator('li', { hasText: file })
    await expect(card.getByRole('button', { name: /See analysis/ })).toBeVisible({ timeout: 60_000 })
    await expect(card).toContainText('Organiser, 1 face')

    await guest.getByRole('button', { name: 'People', exact: true }).click()
    await guest.getByRole('searchbox').fill(name)
    await guest.getByRole('button', { name: /^You/ }).click()
    await expect(guest.getByRole('heading', { name })).toBeVisible()
    await expect(guest.getByText('1 video', { exact: true })).toBeVisible()
    await removeMe(guest)
    await visitor.close()
  })

  test('from a folder, only pictures and videos go, however deep', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const rows = panel.getByRole('listitem')

    const folder = mkdtempSync(join(tmpdir(), 'gallery-folder-'))
    mkdirSync(join(folder, 'Day 1', 'Deeper'), { recursive: true })
    mkdirSync(join(folder, '.Trashes'))
    writeFileSync(join(folder, 'Day 1', 'Deeper', 'deep.jpg'), portrait('portrait-wide.jpg').buffer)
    writeFileSync(join(folder, 'Day 1', '._deep.jpg'), randomBytes(16))
    writeFileSync(join(folder, '.Trashes', 'gone.jpg'), randomBytes(16))
    writeFileSync(join(folder, '.DS_Store'), randomBytes(16))
    // Chromium gives this no type, so its name has to keep it. These bytes are no picture: the server says so.
    writeFileSync(join(folder, 'Day 1', 'phone.HEIC'), randomBytes(64))
    writeFileSync(join(folder, 'Day 1', 'camera.cr2'), randomBytes(64))
    // More than one read of a dropped folder: a browser hands over a hundred entries at a time.
    for (let index = 0; index < 130; index += 1) writeFileSync(join(folder, `note-${index}.txt`), 'not a picture')
    const expectFolderSorted = async () => {
      await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
      await expect(panel.getByText('131 other files were skipped.')).toBeVisible()
      await expect(rows.filter({ hasText: 'deep.jpg' })).toHaveCount(1)
      await expect(rows.filter({ hasText: 'deep.jpg' })).toContainText('Uploaded')
      await expect(rows.filter({ hasText: 'phone.HEIC' })).toContainText('Upload a photo (JPEG')
    }

    // By keyboard, as the button is all a keyboard has.
    const chooser = page.waitForEvent('filechooser')
    await panel.getByRole('button', { name: 'Choose a folder' }).press('Enter')
    await (await chooser).setFiles(folder)
    await expectFolderSorted()
    await expect(rows).toHaveCount(2)

    await panel.getByRole('button', { name: 'Clear the list' }).click()
    await expect(rows).toHaveCount(0)
    await expect(panel.getByText('other files were skipped.')).toBeHidden()
    await expect(panel.getByRole('button', { name: 'Choose photos and videos' })).toBeFocused()

    // The same folder dropped, with a loose file beside it. The folder itself never becomes a row.
    const loose = join(mkdtempSync(join(tmpdir(), 'gallery-loose-')), 'loose.txt')
    writeFileSync(loose, 'not a picture')
    await dropOnPanel(page, [folder, loose])
    await expectFolderSorted()
    await expect(rows).toHaveCount(3)
    // A file picked by hand gets the server's answer, and sending it again cannot change that.
    await expect(panel.getByText('2 files could not be sent.')).toBeVisible()
    await expect(rows.filter({ hasText: 'loose.txt' })).toContainText('Upload a photo (JPEG')
    await expect(panel.getByRole('button', { name: 'Try failed files again' })).toHaveCount(0)
  })

  test('a batch carries on around the console, and a refused one can be sent again', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const warns = () =>
      page.evaluate(() => {
        const leaving = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(leaving)
        return leaving.defaultPrevented
      })
    expect(await warns()).toBe(false)

    // The last step waits, so the upload is still going while the organiser looks elsewhere.
    const complete = '**/api/v1/admin/gallery/uploads/*/complete'
    let letGo = () => {}
    const held = new Promise<void>((resolve) => {
      letGo = resolve
    })
    await page.route(complete, async (route) => {
      await held
      await route.continue()
    })
    const chooser = page.waitForEvent('filechooser')
    await panel.getByRole('button', { name: 'Choose photos and videos' }).press('Enter')
    await (await chooser).setFiles(portrait('portrait-wide.jpg'))
    await expect(panel.getByText('0 of 1 uploaded.')).toBeVisible()
    await expect(panel.getByRole('progressbar', { name: 'Upload progress' })).toBeVisible()
    expect(await warns()).toBe(true)

    await page.getByRole('link', { name: 'Audit log' }).click()
    await page.waitForURL('**/admin/audit')
    expect(await warns()).toBe(true)
    await page.getByRole('link', { name: 'Photo gallery' }).click()
    await page.waitForURL('**/admin/gallery')
    await expect(panel.getByText('0 of 1 uploaded.')).toBeVisible()
    letGo()
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    expect(await warns()).toBe(false)
    await page.unroute(complete)
    await panel.getByRole('button', { name: 'Clear the list' }).click()

    // The server says no to the session, not to the file: the file stays, and goes when asked.
    const start = '**/api/v1/admin/gallery/uploads'
    await page.route(start, (route) => route.fulfill({ status: 403, json: { error: { code: 'FORBIDDEN', message: 'Not right now.' } } }))
    await panel.getByLabel('Choose photos and videos').setInputFiles([
      { ...portrait('portrait-wide.jpg'), name: 'same-name.jpg' },
      { ...portrait('portrait-wide.jpg'), name: 'same-name.jpg' },
    ])
    await expect(panel.getByText('2 files could not be sent.')).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Try same-name.jpg again', exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Try same-name.jpg (2) again', exact: true })).toBeVisible()
    await page.unroute(start)
    await panel.getByRole('button', { name: 'Try failed files again' }).click()
    await expect(panel.getByText('2 files uploaded.')).toBeVisible({ timeout: 30_000 })
  })

  test('with S3 on, files go straight there, and through the server when S3 is blocked', async ({ page, browser, baseURL }) => {
    // No bucket in this stack: these stand in for S3 and the API's part of it, for organisers and guests alike.
    const id = 'a'.repeat(32)
    const fakeS3 = async (on: Page, root: string) => {
      const steps: string[] = []
      await on.route(`**/api/v1${root}`, (route) => {
        if (!route.request().postDataJSON().direct) return route.continue()
        steps.push('start')
        return route.fulfill({ status: 201, json: { upload_id: id, part_size: 5 * 1024 * 1024, mode: 's3' } })
      })
      await on.route(`**/api/v1${root}/${id}/parts`, (route) => {
        steps.push('sign')
        return route.fulfill({ json: { urls: { '1': '/fake-s3/1' } } })
      })
      await on.route(`**/api/v1${root}/${id}/complete`, (route) => {
        steps.push('complete')
        return route.fulfill({ status: 201, json: { status: 'uploaded', duplicate: false, id: 'b' } })
      })
      return steps
    }

    await consoleThinksS3IsOn(page)
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const steps = await fakeS3(page, '/admin/gallery/uploads')
    let parts = 0
    await page.route('**/fake-s3/1', (route) => {
      parts += 1
      return route.fulfill({ status: 200 })
    })
    await panel.getByLabel('Choose photos and videos').setInputFiles(portrait('portrait-wide.jpg'))
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    expect(steps).toEqual(['start', 'sign', 'complete'])
    expect(parts).toBe(1)

    await panel.getByRole('button', { name: 'Clear the list' }).click()
    await page.unroute('**/fake-s3/1')
    await page.route('**/fake-s3/1', (route) => route.abort())
    await panel.getByLabel('Choose photos and videos').setInputFiles({ ...portrait('portrait-wide.jpg'), name: 'blocked.jpg' })
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('region', { name: 'Files', exact: true }).locator('li', { hasText: 'blocked.jpg' })).toContainText('Organiser')

    const visitor = await browser.newContext({ baseURL })
    const guest = await visitor.newPage()
    await guest.route('**/api/v1/gallery/me', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const response = await route.fetch()
      await route.fulfill({ response, json: { ...(await response.json()), direct_uploads: true } })
    })
    await guest.goto('/gallery')
    await fillForm(guest, `Direct guest ${Date.now().toString(36)}`)
    await guest.getByRole('button', { name: 'Continue' }).click()
    await expect(uploadHeading(guest)).toBeVisible({ timeout: 30_000 })
    const guestSteps = await fakeS3(guest, '/gallery/uploads')
    await guest.route('**/fake-s3/1', (route) => route.fulfill({ status: 200 }))
    await guest.getByLabel('Choose photos and videos').setInputFiles(portrait('portrait-wide.jpg'))
    await expect(guest.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    expect(guestSteps).toEqual(['start', 'sign', 'complete'])
    await removeMe(guest)
    await visitor.close()
  })
})

test.describe('organisers upload from the console, when things go wrong', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-chromium', 'admin flows run on desktop'))

  test('signing out in the middle of a batch asks first, and the way out does not ask again', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const browserAsked: string[] = []
    page.on('dialog', (dialog) => {
      browserAsked.push(dialog.type())
      void dialog.dismiss()
    })
    let logouts = 0
    page.on('request', (request) => {
      if (request.url().endsWith('/api/v1/admin/auth/logout')) logouts += 1
    })

    // The last step never ends, so the upload is still going when the organiser reaches for the button.
    await page.route('**/api/v1/admin/gallery/uploads/*/complete', () => {})
    await panel.getByLabel('Choose photos and videos').setInputFiles([
      { ...portrait('portrait-wide.jpg'), name: 'first.jpg' },
      { ...portrait('portrait-wide.jpg'), name: 'second.jpg' },
    ])
    await expect(panel.getByText('0 of 2 uploaded.')).toBeVisible()

    // Changing their mind leaves the upload and the session as they were.
    const signOut = page.getByRole('button', { name: 'Sign out' })
    const question = page.getByRole('dialog', { name: 'Stop the upload and sign out?' })
    await signOut.click()
    await expect(question).toBeVisible()
    await question.getByRole('button', { name: 'Cancel' }).click()
    await expect(question).toBeHidden()
    expect(logouts).toBe(0)
    await expect(panel.getByText('0 of 2 uploaded.')).toBeVisible()

    // A real navigation asks while files are going, so nothing asked below means something.
    await page.evaluate(() => window.location.reload())
    await expect.poll(() => browserAsked).toEqual(['beforeunload'])
    await expect(panel.getByText('0 of 2 uploaded.')).toBeVisible()
    browserAsked.length = 0

    await signOut.click()
    await question.getByRole('button', { name: 'Stop and sign out' }).click()
    await expect(page).toHaveURL(/\/admin\/login\?signed_out=1$/)
    expect(browserAsked).toEqual([])
    expect(logouts).toBe(1)

    // Nothing of that batch reached the gallery.
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    await expect(page.getByRole('region', { name: 'Files', exact: true }).getByText('Nothing shared yet')).toBeVisible()
  })

  test('with nothing going, signing out does not ask', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    await uploadPanel(page).getByLabel('Choose photos and videos').setInputFiles(portrait('portrait-wide.jpg'))
    await expect(uploadPanel(page).getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/admin\/login\?signed_out=1$/)
  })

  test('a file removed while it waits for another try stays away', async ({ page }) => {
    await consoleThinksS3IsOn(page)
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const root = '**/api/v1/admin/gallery/uploads'
    const id = 'c'.repeat(32)
    const steps: string[] = []
    let partSize = 0
    await page.route(root, (route) => {
      steps.push('start')
      return route.fulfill({ status: 201, json: { upload_id: id, part_size: partSize, mode: 's3' } })
    })
    await page.route(`${root}/${id}/parts`, (route) => {
      steps.push('sign')
      const urls = Object.fromEntries((route.request().postDataJSON().numbers as number[]).map((number) => [number, `/fake-s3/${number}`]))
      return route.fulfill({ json: { urls } })
    })
    await page.route(`${root}/${id}/complete`, (route) => {
      steps.push('complete')
      return route.fulfill({ status: 201, json: { status: 'uploaded', duplicate: false, id: 'b' } })
    })
    await page.route(`${root}/${id}`, (route) => {
      steps.push('abort')
      return route.fulfill({ status: 204 })
    })
    const rows = panel.getByRole('listitem')

    // The whole file failed, and the queue waits two seconds before sending it again.
    const file = { ...portrait('portrait-wide.jpg'), name: 'whole-file.jpg' }
    partSize = file.buffer.length
    await page.route('**/fake-s3/*', (route) => {
      steps.push('part')
      return route.fulfill({ status: 503 })
    })
    await panel.getByLabel('Choose photos and videos').setInputFiles(file)
    await expect.poll(() => steps).toEqual(['start', 'sign', 'part', 'abort'])
    await panel.getByRole('button', { name: 'Remove whole-file.jpg from the upload list' }).click()
    await expect(rows).toHaveCount(0)
    await page.waitForTimeout(3000)
    expect(steps).toEqual(['start', 'sign', 'part', 'abort'])
    await page.unroute('**/fake-s3/*')

    // One part got there and the last one dropped: only that part waits, a second, with nothing else in flight.
    steps.length = 0
    const parts = { ...portrait('portrait-wide.jpg'), name: 'two-parts.jpg' }
    partSize = Math.ceil(parts.buffer.length / 2)
    let firstArrived = () => {}
    const first = new Promise<void>((resolve) => {
      firstArrived = resolve
    })
    await page.route('**/fake-s3/1', async (route) => {
      steps.push('part 1')
      await route.fulfill({ status: 200 })
      firstArrived()
    })
    await page.route('**/fake-s3/2', async (route) => {
      steps.push('part 2')
      await first
      await page.waitForTimeout(300) // the page has heard that part 1 arrived
      if (steps.filter((step) => step === 'part 2').length > 1) return route.fulfill({ status: 200 })
      await route.abort()
      steps.push('dropped')
    })
    await panel.getByLabel('Choose photos and videos').setInputFiles(parts)
    await expect.poll(() => steps).toContain('dropped')
    await panel.getByRole('button', { name: 'Remove two-parts.jpg from the upload list' }).click()
    await expect(rows).toHaveCount(0)
    await expect.poll(() => steps).toContain('abort')
    await page.waitForTimeout(3000)
    expect(steps.filter((step) => step === 'part 2')).toHaveLength(1)
    expect(steps).not.toContain('complete')
  })

  test('a tab that still thinks S3 is on sends through the server once it is off', async ({ page }) => {
    await consoleThinksS3IsOn(page)
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const steps: string[] = []
    page.on('request', (request) => {
      const [, tail] = /\/admin\/gallery\/uploads(.*)$/.exec(request.url()) ?? []
      if (tail === undefined) return
      const shape = tail.replace(/^\/[a-f0-9]{32}/, '/id').replace(/\?.*$/, '')
      steps.push(`${request.method()} ${shape || '/'}${request.method() === 'POST' && !shape && request.postDataJSON().direct ? ' direct' : ''}`)
    })
    await panel.getByLabel('Choose photos and videos').setInputFiles({ ...portrait('portrait-wide.jpg'), name: 'after-s3.jpg' })
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    // The first upload is dropped without waiting, so that request can overtake the next one.
    expect(steps).toContain('DELETE /id')
    expect(steps.filter((step) => step !== 'DELETE /id')).toEqual(['POST / direct', 'POST /', 'PUT /id', 'POST /id/complete'])
    await expect(page.getByRole('region', { name: 'Files', exact: true }).locator('li', { hasText: 'after-s3.jpg' })).toContainText('Organiser')
  })

  test('a batch that is refused keeps the list short and says why', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const start = '**/api/v1/admin/gallery/uploads'
    let asked = 0
    await page.route(start, (route) => {
      asked += 1
      // One file's own problem, and everyone else's the same.
      if (asked === 1) return route.fulfill({ status: 413, json: { error: { code: 'FILE_TOO_LARGE', message: 'Photos must be 20 MB or smaller.' } } })
      return route.fulfill({ status: 507, json: { error: { code: 'INSUFFICIENT_STORAGE', message: 'The gallery is out of space for now. Tell the organisers.' } } })
    })
    const rows = panel.getByRole('listitem')
    await panel.getByLabel('Choose photos and videos').setInputFiles(Array.from({ length: 27 }, (_, index) => ({ ...portrait('portrait-wide.jpg'), name: `refused-${index}.jpg` })))
    await expect(panel.getByText('27 files could not be sent.')).toBeVisible({ timeout: 30_000 })
    await expect(rows).toHaveCount(24)
    await expect(panel.getByText('3 more could not be sent.')).toBeVisible()
    await expect(panel.getByText('The list says why.')).toHaveCount(0)
    await expect(panel.getByText('26 of them: The gallery has run out of space.')).toBeVisible()
    await expect(panel.getByText('1 of them: Photos must be 20 MB or smaller.')).toBeVisible()

    // The ones out of sight go too. The refused one was told no about itself, so it stays out.
    await page.unroute(start)
    await panel.getByRole('button', { name: 'Try failed files again' }).click()
    await expect(panel.getByText('26 files uploaded.')).toBeVisible({ timeout: 60_000 })
    await expect(panel.getByText('1 file could not be sent. The list says why.')).toBeVisible()
  })

  for (const { panelName, path, what } of [
    { panelName: 'Files', path: 'media', what: 'files' },
    { panelName: 'People', path: 'people', what: 'people' },
  ]) {
    test(`a page of ${what} that fails to load says so, and the buttons still work`, async ({ page }) => {
      let failures = 1
      const list = `**/api/v1/admin/gallery/${path}?*`
      await page.route(list, (route) => {
        const offset = new URL(route.request().url()).searchParams.get('offset')
        if (offset === '40' && failures > 0) {
          failures -= 1
          return route.fulfill({ status: 500, json: { error: { code: 'INTERNAL_ERROR', message: 'Something went wrong.' } } })
        }
        return route.fulfill({ json: { [path]: [], total: 100, pending: 0 } })
      })
      await signInAsOrganiser(page)
      await page.goto('/admin/gallery')
      const panel = page.getByRole('region', { name: panelName, exact: true })
      const nav = panel.getByRole('navigation', { name: panelName })
      await expect(nav.getByText('Page 1 of 3')).toBeVisible()
      await nav.getByRole('button', { name: 'Next' }).click()

      await expect(panel.getByRole('alert')).toContainText(`Could not load the ${what}`)
      await expect(nav.getByRole('button', { name: 'Previous' })).toBeEnabled()
      await expect(nav.getByRole('button', { name: 'Next' })).toBeEnabled()

      await panel.getByRole('button', { name: 'Try again' }).click()
      await expect(panel.getByRole('alert')).toBeHidden()
      await expect(nav.getByText('Page 2 of 3')).toBeVisible()
    })
  }

  test('a folder that cannot be read to the end is not called a skipped file', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    const folder = mkdtempSync(join(tmpdir(), 'gallery-locked-'))
    mkdirSync(join(folder, 'locked'))
    writeFileSync(join(folder, 'open.jpg'), portrait('portrait-wide.jpg').buffer)
    writeFileSync(join(folder, 'locked', 'out-of-reach.jpg'), portrait('portrait-wide.jpg').buffer)
    // This container runs as root, which no folder keeps out, so the browser is told what a locked one does.
    await page.evaluate(() => {
      const realEntry = DataTransferItem.prototype.webkitGetAsEntry
      DataTransferItem.prototype.webkitGetAsEntry = function () {
        const entry = realEntry.call(this)
        if (entry?.isDirectory) {
          const kind = Object.getPrototypeOf(entry) as FileSystemDirectoryEntry
          const realReader = kind.createReader
          kind.createReader = function () {
            if (this.name !== 'locked') return realReader.call(this)
            return { readEntries: (_found, failed) => failed?.(new DOMException('Not readable.', 'NotReadableError')) }
          }
          DataTransferItem.prototype.webkitGetAsEntry = realEntry
        }
        return entry
      }
    })
    await dropOnPanel(page, [folder])
    await expect(panel.getByText('1 file uploaded.')).toBeVisible({ timeout: 30_000 })
    await expect(panel.getByText('A folder could not be read to the end, so some files were not sent. Choose it again.')).toBeVisible()
    await expect(panel.getByText('skipped')).toHaveCount(0)
    await expect(panel.getByRole('listitem').filter({ hasText: 'out-of-reach.jpg' })).toHaveCount(0)
    await panel.getByRole('button', { name: 'Clear the list' }).click()
    await expect(panel.getByText('A folder could not be read to the end')).toBeHidden()
  })
})

test.describe('layout', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'mobile-chromium', 'the width sweep runs once'))

  test('on a touch screen the upload panel asks for files only', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    const panel = uploadPanel(page)
    await expect(panel.getByRole('button', { name: 'Choose photos and videos' })).toBeVisible()
    await expect(panel.getByRole('button', { name: 'Choose a folder' })).toBeHidden()
    await expect(panel.getByText('Drop photos, videos or whole folders here')).toBeHidden()
    await noHorizontalOverflow(page)
  })

  test('on a phone the sign-out question shows over the navigation', async ({ page }) => {
    await signInAsOrganiser(page)
    await page.goto('/admin/gallery')
    await page.route('**/api/v1/admin/gallery/uploads/*/complete', () => {})
    const panel = uploadPanel(page)
    await panel.getByLabel('Choose photos and videos').setInputFiles(portrait('portrait-wide.jpg'))
    await expect(panel.getByText('0 of 1 uploaded.')).toBeVisible()
    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('dialog', { name: 'Navigation' }).getByRole('button', { name: 'Sign out' }).click()
    const question = page.getByRole('dialog', { name: 'Stop the upload and sign out?' })
    await expect(question).toBeVisible()
    await noHorizontalOverflow(page)
    await question.getByRole('button', { name: 'Cancel' }).click()
    await expect(question).toBeHidden()
    await expect(page.getByRole('dialog', { name: 'Navigation' })).toBeVisible()
    await page.getByRole('dialog', { name: 'Navigation' }).getByRole('button', { name: 'Sign out' }).click()
    await question.getByRole('button', { name: 'Stop and sign out' }).click()
    await expect(page).toHaveURL(/\/admin\/login\?signed_out=1$/)
  })

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
