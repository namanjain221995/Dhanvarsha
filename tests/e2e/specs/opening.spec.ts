import { fileURLToPath } from 'node:url'
import zlib from 'node:zlib'
import { type Page, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { noHorizontalOverflow } from '../lib/participants'

/*
 * The opening page (/opening): the public showcase for the projector and the LED wall, and its
 * editor in the organiser console. By default it is a game show: the cards lie face down with
 * their numbers until one is scratched open (or revealed with the Reveal button) in a big card
 * in the middle. Every test starts from the built-in defaults and the editing tests put them
 * back, so nothing here depends on another spec.
 *
 * For the presenter: a full-screen button (and the F key), and per card an uploaded picture or
 * video that the card's button shows full screen in a viewer above everything.
 */

/** The 13 built-in cards, in order (the API's defaults). */
const DEFAULT_CARDS = [
  'The call that changes everything',
  'Techsara placement League',
  'Candidate Ki Adalat',
  'Training Challenge — Choose One',
  'Snake and Leaders',
  'Roast-o-Sphere — Error 404 Not Found',
  'Kaun Banega Policy-Pati?',
  'Leader’s Challenge — Managers’ Catan Game',
  'TechSara Shark Tank',
  'Owners’ Conversation',
  'Employee Gratitude — Gratitude Ceremony Videos',
  'Because of Techsara',
  'Professional Honours',
]

const tag = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`

type PublicOpening = {
  title: string
  year: string
  tagline: string
  button_label: string
  hidden: boolean
  reveal_epoch: number
  logo_url: string | null
  cards: {
    id: string
    title: string
    icon: string
    image_url: string | null
    link: string
    media: { url: string; type: 'image' | 'video' } | null
  }[]
}

/** A 1 s, 64 × 64 VP8 WebM (a gold square moving across night blue), made with ffmpeg. */
const CLIP = fileURLToPath(new URL('../fixtures/clip.webm', import.meta.url))

/** A small PNG made here: a navy-to-gold gradient, `width` × `height`. */
function gradientPng(width: number, height: number): Buffer {
  const table = Array.from({ length: 256 }, (_, n) => {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc32 = (data: Buffer) => {
    let c = 0xffffffff
    for (const byte of data) c = table[(c ^ byte) & 0xff]! ^ (c >>> 8)
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
  const raw = Buffer.alloc((width * 3 + 1) * height)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const t = x / Math.max(1, width - 1)
      raw.set([Math.round(5 + 237 * t), Math.round(12 + 181 * t), Math.round(44 + 34 * t)], y * (width * 3 + 1) + 1 + x * 3)
    }
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // truecolour RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

/** Sign the browser context in as the organiser through the API (fast path). */
async function organiserSession(page: Page, baseURL: string) {
  const res = await page.request.post('/api/v1/admin/auth/login', {
    headers: { Origin: baseURL },
    data: { email: process.env.ADMIN_EMAIL ?? '', password: process.env.ADMIN_PASSWORD ?? '' },
  })
  expect(res.status(), await res.text()).toBe(200)
}

async function resetToDefaults(baseURL: string) {
  const admin = await Admin.login(baseURL)
  try {
    await admin.resetOpening()
  } finally {
    await admin.dispose()
  }
}

const cards = (page: Page) => page.getByRole('list', { name: 'Programme' }).getByRole('listitem')
const cardTitles = (page: Page) => cards(page).getByRole('heading', { level: 2 })
/** The gold pills: links when a card has one, decoration (hidden from assistive technology) otherwise. */
const pills = (page: Page) => page.locator('[data-opening-play]')
const cardGroup = (page: Page, n: number) => page.getByRole('group', { name: `Card ${n}`, exact: true })
/** A face-down card in the grid: its number only. */
const hiddenCard = (page: Page, n: number) => page.getByRole('button', { name: `Card ${n}, hidden. Open to reveal.`, exact: true })
const hiddenCards = (page: Page) => page.getByRole('button', { name: /^Card \d+, hidden\. Open to reveal\.$/ })
/** A revealed card in the grid, which opens big again on its face. */
const revealedCard = (page: Page, n: number, title: string) =>
  page.getByRole('button', { name: `Card ${n}: ${title}. Show it big.`, exact: true })
const bigCard = (page: Page, n: number) => page.getByRole('dialog', { name: `Card ${n}`, exact: true })
/** The media viewer: a dialog named after the card. */
const viewer = (page: Page, title: string) => page.getByRole('dialog', { name: title, exact: true })
/** The editor's "Show on button click" field of card n. */
const mediaField = (page: Page, n: number) => cardGroup(page, n).getByRole('group', { name: /^Show on button click/ })
const fullscreenElement = (page: Page) => page.evaluate(() => document.fullscreenElement?.tagName ?? null)

async function publicOpening(page: Page): Promise<PublicOpening> {
  const res = await page.request.get('/api/v1/public/opening')
  expect(res.status(), await res.text()).toBe(200)
  return (await res.json()) as PublicOpening
}

/** Wait for the fonts and the entrance animations (the coins, stars and halo run for ever). */
async function settled(page: Page) {
  await page.evaluate(() => document.fonts.ready)
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      .every((a) => a.playState === 'finished'),
  )
}

test.describe('opening page', () => {
  test.beforeEach(async ({ baseURL }, info) => {
    test.skip(info.project.name !== 'desktop-chromium', 'runs once, on desktop (the layout test sets its own sizes)')
    await resetToDefaults(baseURL!)
  })

  test('shows the 13 default cards face down, without numbers, with nothing of their titles in the page', async ({ page }) => {
    const data = await publicOpening(page)
    expect(data.cards.map((card) => card.title)).toEqual(DEFAULT_CARDS)
    expect(data.button_label).toBe('PLAY GAME')
    expect(data.hidden).toBe(true)
    expect(Number.isInteger(data.reveal_epoch) && data.reveal_epoch >= 0).toBe(true)

    await page.goto('/opening')
    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toContainText(data.title)
    if (data.year) await expect(heading).toContainText(data.year)
    await expect(page).toHaveTitle(`${[data.title, data.year].filter(Boolean).join(' ')} · Opening`)
    if (data.tagline) await expect(page.getByText(data.tagline.split('\n')[0]!.trim())).toBeVisible()

    await expect(cards(page)).toHaveCount(13)
    await expect(hiddenCards(page)).toHaveCount(13)
    for (let n = 1; n <= 13; n += 1) {
      await expect(hiddenCard(page, n)).toBeVisible()
      // The backs are all alike: no number on them (the accessible name still counts them).
      expect((await cards(page).nth(n - 1).innerText()).trim()).toBe('')
    }
    // No spoilers: no titles, icons' buttons or links, in the text or anywhere in the page.
    await expect(cardTitles(page)).toHaveCount(0)
    await expect(pills(page)).toHaveCount(0)
    await expect(page.getByRole('link')).toHaveCount(0)
    const text = await page.locator('body').innerText()
    const html = await page.content()
    for (const title of DEFAULT_CARDS) {
      expect(text, title).not.toContain(title)
      expect(html, title).not.toContain(title)
    }
  })

  test('a card opens big, is revealed, stays revealed after a reload, and the organiser hides it again', async ({ page, context, baseURL }) => {
    const title = DEFAULT_CARDS[1]!
    await page.goto('/opening')
    await hiddenCard(page, 2).click()
    const dialog = bigCard(page, 2)
    await expect(dialog).toBeVisible()
    // Under the foil there is nothing to read yet; focus waits on the Reveal button.
    await expect(dialog.getByRole('heading')).toHaveCount(0)
    expect(await dialog.innerHTML()).not.toContain(title)
    const reveal = dialog.getByRole('button', { name: 'Reveal', exact: true })
    await expect(reveal).toBeFocused()
    await reveal.click()
    await expect(dialog.getByRole('heading', { level: 2, name: title })).toBeVisible()
    await expect(dialog.getByRole('status')).toHaveText(`Card 2: ${title}`)
    await expect(reveal).toHaveCount(0)

    // A click on the dimmed page around the card puts it back; the grid now shows its face.
    await page.mouse.click(8, Math.round(page.viewportSize()!.height / 2))
    await expect(dialog).toBeHidden()
    await expect(cardTitles(page)).toHaveText([title])
    await expect(revealedCard(page, 2, title)).toBeFocused()
    await expect(hiddenCards(page)).toHaveCount(12)

    // This browser remembers it.
    await page.reload()
    await expect(cardTitles(page)).toHaveText([title])
    await expect(hiddenCards(page)).toHaveCount(12)

    // A revealed card opens straight on its face; Escape puts it back.
    await revealedCard(page, 2, title).click()
    await expect(dialog.getByRole('heading', { level: 2, name: title })).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Reveal', exact: true })).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(revealedCard(page, 2, title)).toBeFocused()

    // The organiser hides every card again: the screen follows at its next refresh (10 s).
    const epoch = (await publicOpening(page)).reveal_epoch
    const consolePage = await context.newPage()
    await organiserSession(consolePage, baseURL!)
    await consolePage.goto('/admin/opening')
    await consolePage.getByRole('button', { name: 'Hide all cards again' }).click()
    const confirm = consolePage.getByRole('dialog', { name: 'Hide all cards again?' })
    await expect(confirm).toContainText('Every screen showing the opening page hides all cards again within 10 seconds.')
    await confirm.getByRole('button', { name: 'Hide all cards', exact: true }).click()
    await expect(confirm).toBeHidden()
    await expect(consolePage.locator('[aria-live]').filter({ hasText: 'All cards are hidden again.' }).first()).toBeVisible()
    expect((await publicOpening(page)).reveal_epoch).toBe(epoch + 1)

    await expect(cardTitles(page)).toHaveCount(0, { timeout: 20_000 })
    await expect(hiddenCards(page)).toHaveCount(13)
    await page.reload()
    await expect(hiddenCards(page)).toHaveCount(13)
    await expect(cardTitles(page)).toHaveCount(0)
  })

  test('the organiser renames the button and a card, moves it up, and the page follows', async ({ page, context, baseURL }) => {
    const t = tag()
    const label = `GO ${t.toUpperCase()}`
    const renamed = `Quiz Arena ${t}`
    await organiserSession(page, baseURL!)
    try {
      await page.goto('/admin')
      await page.getByRole('navigation', { name: 'Console' }).getByRole('link', { name: 'Opening page' }).click()
      await expect(page).toHaveURL(/\/admin\/opening$/)
      await expect(page.getByRole('heading', { level: 1, name: 'Opening page' })).toBeVisible()
      await expect(cardGroup(page, 1).getByLabel('Title', { exact: true })).toHaveValue(DEFAULT_CARDS[0]!)
      await expect(page.getByLabel('Button text')).toHaveValue('PLAY GAME')

      // Cards shown as they are, not face down.
      const hiding = page.getByRole('switch', { name: 'Hide cards until they are revealed (scratch card)' })
      await expect(hiding).toHaveAttribute('aria-checked', 'true')
      await hiding.click()
      await expect(hiding).toHaveAttribute('aria-checked', 'false')
      await page.getByLabel('Button text').fill(label)
      await cardGroup(page, 2).getByLabel('Title', { exact: true }).fill(renamed)
      await page.getByRole('button', { name: 'Move card 2 up' }).click()
      await expect(cardGroup(page, 1).getByLabel('Title', { exact: true })).toHaveValue(renamed)
      await expect(cardGroup(page, 2).getByLabel('Title', { exact: true })).toHaveValue(DEFAULT_CARDS[0]!)
      await expect(page.getByText('You have unsaved changes')).toBeVisible()

      await page.getByRole('button', { name: 'Save changes' }).click()
      await expect(page.locator('[aria-live]').filter({ hasText: 'Saved.' }).first()).toBeVisible()
      await expect(page.getByText('You have unsaved changes')).toBeHidden()

      // Saved on the server, in the new order.
      const admin = await Admin.login(baseURL!)
      const saved = await admin.opening()
      await admin.dispose()
      expect(saved.opening.button_label).toBe(label)
      expect(saved.opening.hidden).toBe(false)
      expect(saved.opening.cards.slice(0, 2).map((card) => card.title)).toEqual([renamed, DEFAULT_CARDS[0]])

      // The public page shows every card face up, with the new label and in the new order.
      const screen = await context.newPage()
      await screen.goto('/opening')
      await expect(cardTitles(screen)).toHaveText([renamed, DEFAULT_CARDS[0]!, ...DEFAULT_CARDS.slice(2)])
      await expect(pills(screen)).toHaveText(Array(13).fill(label))
      await expect(hiddenCards(screen)).toHaveCount(0)

      // Reset to defaults, after a confirmation, and the page follows again.
      await page.getByRole('button', { name: 'Reset to defaults' }).click()
      const dialog = page.getByRole('dialog', { name: 'Reset the opening page?' })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: 'Reset to defaults' }).click()
      await expect(dialog).toBeHidden()
      await expect(page.locator('[aria-live]').filter({ hasText: 'back to the defaults' }).first()).toBeVisible()
      await expect(cardGroup(page, 1).getByLabel('Title', { exact: true })).toHaveValue(DEFAULT_CARDS[0]!)
      await expect(page.getByLabel('Button text')).toHaveValue('PLAY GAME')
      await expect(hiding).toHaveAttribute('aria-checked', 'true')

      // The defaults hide the cards again.
      await screen.reload()
      await expect(hiddenCards(screen)).toHaveCount(13)
      await expect(cardTitles(screen)).toHaveCount(0)
    } finally {
      await resetToDefaults(baseURL!)
    }
  })

  test('with hiding off, every card shows its title and button', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    try {
      const saved = await admin.saveOpening({ hidden: false })
      expect(saved.opening.hidden).toBe(false)
    } finally {
      await admin.dispose()
    }
    try {
      expect((await publicOpening(page)).hidden).toBe(false)
      await page.goto('/opening')
      await expect(cardTitles(page)).toHaveText(DEFAULT_CARDS)
      await expect(pills(page)).toHaveText(Array(13).fill('PLAY GAME'))
      await expect(hiddenCards(page)).toHaveCount(0)
      // The default cards have no links: their pills are decoration, never a dead link or a focus stop.
      await expect(page.getByRole('link')).toHaveCount(0)
      await expect(pills(page).first()).toHaveAttribute('aria-hidden', 'true')
      expect(await pills(page).first().evaluate((el) => el.tagName)).toBe('SPAN')
    } finally {
      await resetToDefaults(baseURL!)
    }
  })

  test('the organiser gives two cards a picture and a video, and their buttons show them full screen', async ({ page, context, baseURL }) => {
    const [pictureTitle, videoTitle] = [DEFAULT_CARDS[0]!, DEFAULT_CARDS[1]!]
    await organiserSession(page, baseURL!)
    try {
      await page.goto('/admin/opening')
      await expect(page.getByRole('heading', { level: 1, name: 'Opening page' })).toBeVisible()
      await expect(cardGroup(page, 1).getByText('Used only when the card has no image or video.')).toBeVisible()

      // Card 1: a picture, made here. Card 2: a short real video, sent in chunks.
      const picture = mediaField(page, 1)
      await picture.locator('input[type=file]').setInputFiles({ name: 'stage.png', mimeType: 'image/png', buffer: gradientPng(320, 180) })
      await expect(picture.locator('img[src^="/media/"]')).toHaveCount(1)
      await expect(picture).toContainText('stage.png')
      const clip = mediaField(page, 2)
      await clip.locator('input[type=file]').setInputFiles(CLIP)
      await expect(clip.locator('video[src^="/media/"]')).toHaveCount(1)
      await expect(clip).toContainText('clip.webm')
      await expect(clip.getByRole('alert')).toHaveCount(0)

      // A file that is neither: the API's message shows in the field, nothing changes.
      const third = mediaField(page, 3)
      await third.locator('input[type=file]').setInputFiles({ name: 'notes.mp4', mimeType: 'video/mp4', buffer: Buffer.from('<html>not a video</html>') })
      await expect(third.getByRole('alert')).toContainText('MP4 or WebM')
      await expect(third.getByRole('button', { name: 'Upload image or video' })).toBeVisible()

      await page.getByRole('button', { name: 'Save changes' }).click()
      await expect(page.locator('[aria-live]').filter({ hasText: 'Saved.' }).first()).toBeVisible()

      // Saved: a WebP picture and the video as it was sent, by name for the organiser only.
      const admin = await Admin.login(baseURL!)
      const saved = await admin.opening()
      await admin.dispose()
      const [first, second] = saved.opening.cards
      expect(first!.media).toMatchObject({ type: 'image', name: 'stage.png' })
      expect(first!.media!.url).toMatch(/^\/media\/[a-f0-9]{32}\.webp$/)
      expect(second!.media).toMatchObject({ type: 'video', name: 'clip.webm' })
      expect(second!.media!.url).toMatch(/^\/media\/[a-f0-9]{32}\.webm$/)
      const data = await publicOpening(page)
      expect(data.cards[0]!.media).toEqual({ url: first!.media!.url, type: 'image' })
      expect(data.cards[1]!.media).toEqual({ url: second!.media!.url, type: 'video' })
      expect(JSON.stringify(data)).not.toContain('stage.png')

      // The screen: reveal card 1, and its button shows the picture full screen.
      const screen = await context.newPage()
      await screen.goto('/opening')
      await hiddenCard(screen, 1).click()
      const big = bigCard(screen, 1)
      await big.getByRole('button', { name: 'Reveal', exact: true }).click()
      const show = big.locator('button[data-opening-play]')
      await expect(show).toHaveAccessibleName(new RegExp(`${pictureTitle}.*picture`))
      await expect(show).toBeFocused()
      await show.click()
      const pictureViewer = viewer(screen, pictureTitle)
      await expect(pictureViewer).toBeVisible()
      const image = pictureViewer.getByRole('img', { name: pictureTitle, exact: true })
      await expect(image).toBeVisible()
      await expect(image).toHaveAttribute('src', first!.media!.url)
      await expect.poll(() => fullscreenElement(screen)).toBe('HTML')

      // A change on the server reaches the page behind; the viewer stays as it is.
      await image.evaluate((element) => element.setAttribute('data-e2e-mark', ''))
      const editor = await Admin.login(baseURL!)
      await editor.saveOpening({ tagline: 'Changed while the picture shows' })
      await editor.dispose()
      await screen.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
      await expect(screen.locator('.opening-tagline')).toHaveText('Changed while the picture shows')
      await expect(pictureViewer).toBeVisible()
      await expect(pictureViewer.locator('img[data-e2e-mark]')).toHaveCount(1)

      // Escape closes the viewer (and gives back the full screen it took); the big card stays.
      await screen.keyboard.press('Escape')
      await expect(pictureViewer).toBeHidden()
      await expect(big).toBeVisible()
      await expect(show).toBeFocused()
      await expect.poll(() => fullscreenElement(screen)).toBeNull()
      await big.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(big).toBeHidden()

      // Card 2: the video plays in the viewer; a click on the black around it closes.
      await hiddenCard(screen, 2).click()
      const big2 = bigCard(screen, 2)
      await big2.getByRole('button', { name: 'Reveal', exact: true }).click()
      const play = big2.locator('button[data-opening-play]')
      await expect(play).toHaveAccessibleName(new RegExp(`${videoTitle}.*video`))
      await play.click()
      const videoViewer = viewer(screen, videoTitle)
      await expect(videoViewer).toBeVisible()
      const video = videoViewer.locator('video')
      await expect(video).toBeVisible()
      await expect(video).toHaveAttribute('src', /^\/media\/[a-f0-9]{32}\.(mp4|webm)$/)
      // Loaded, sized to its own shape (64 × 64), and started by the click.
      await expect(video).toHaveAttribute('data-fit', '')
      await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime > 0 || !v.paused)).toBe(true)
      await expect(videoViewer.getByRole('alert')).toHaveCount(0)
      const viewport = screen.viewportSize()!
      await screen.mouse.click(6, Math.round(viewport.height / 2))
      await expect(videoViewer).toBeHidden()
      await expect(play).toBeFocused()
      await expect.poll(() => fullscreenElement(screen)).toBeNull()

      // With hiding off the grid's buttons open the viewer too.
      const owner = await Admin.login(baseURL!)
      await owner.saveOpening({ hidden: false })
      await owner.dispose()
      await screen.reload()
      const gridButton = cards(screen).nth(0).locator('button[data-opening-play]')
      await gridButton.click()
      await expect(viewer(screen, pictureTitle)).toBeVisible()
      await viewer(screen, pictureTitle).getByRole('button', { name: 'Close', exact: true }).click()
      await expect(viewer(screen, pictureTitle)).toBeHidden()
      await expect(gridButton).toBeFocused()
      // Card 3 has no media and no link: still decoration only.
      expect(await cards(screen).nth(2).locator('[data-opening-play]').evaluate((el) => el.tagName)).toBe('SPAN')
    } finally {
      await resetToDefaults(baseURL!)
    }
  })

  test('the full-screen button and the F key take the page full screen and back', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto('/opening')
    await expect(cards(page)).toHaveCount(13)
    const enter = page.getByRole('button', { name: 'Full screen', exact: true })
    await expect(enter).toBeVisible()
    await expect(enter).toHaveAttribute('title', 'Full screen (F)')
    await enter.click()
    await expect.poll(() => fullscreenElement(page)).toBe('HTML')
    const exit = page.getByRole('button', { name: 'Exit full screen', exact: true })
    await expect(exit).toBeVisible()

    // Still one screen, no scrolling either way.
    await settled(page)
    await noHorizontalOverflow(page)
    expect(await page.evaluate(() => document.documentElement.scrollHeight - document.documentElement.clientHeight)).toBeLessThanOrEqual(0)

    // The pointer rests: the button fades (and the cursor hides); a movement brings it back.
    const root = page.locator('[data-scene="opening"]')
    await expect(root).toHaveAttribute('data-idle', '', { timeout: 6000 })
    await expect(exit).toHaveCSS('opacity', '0')
    await page.mouse.move(300, 300)
    await page.mouse.move(320, 330)
    await expect(root).not.toHaveAttribute('data-idle')
    await expect(exit).toHaveCSS('opacity', '1')
    await exit.click()
    await expect.poll(() => fullscreenElement(page)).toBeNull()
    await expect(enter).toBeVisible()

    // The F key does the same.
    await page.keyboard.press('f')
    await expect.poll(() => fullscreenElement(page)).toBe('HTML')
    await page.keyboard.press('f')
    await expect.poll(() => fullscreenElement(page)).toBeNull()
  })

  test('fits a phone, a laptop, the projector and the LED wall', async ({ page }) => {
    // Every landscape screen shows everything at once: 1536 × 864 is a 1080p laptop at 125 % in
    // full screen, 1024 × 768 a 4:3 projector, 1920 × 872 the 2.2:1 LED wall (22 × 10 ft).
    const sizes = [
      { width: 390, height: 844, oneScreen: false, rows: 0 },
      { width: 1366, height: 768, oneScreen: true, rows: 2 },
      { width: 1536, height: 864, oneScreen: true, rows: 2 },
      { width: 1024, height: 768, oneScreen: true, rows: 3 },
      { width: 1920, height: 1080, oneScreen: true, rows: 2 },
      { width: 1920, height: 872, oneScreen: true, rows: 2 },
    ]
    for (const size of sizes) {
      await page.setViewportSize({ width: size.width, height: size.height })
      await page.goto('/opening')
      await expect(cards(page)).toHaveCount(13)
      await expect(page.getByRole('button', { name: 'Full screen', exact: true })).toBeVisible()
      await settled(page)
      await noHorizontalOverflow(page)
      if (size.oneScreen) {
        const fit = await page.evaluate(() => {
          const doc = document.documentElement
          const boxes = [...document.querySelectorAll('[aria-label="Programme"] > li')].map((li) => li.getBoundingClientRect())
          return {
            scroll: doc.scrollHeight - doc.clientHeight,
            lastBottom: Math.max(...boxes.map((box) => box.bottom)),
            rows: new Set(boxes.map((box) => Math.round(box.top))).size,
            height: window.innerHeight,
          }
        })
        expect(fit.scroll, `vertical scroll at ${size.width}×${size.height}`).toBeLessThanOrEqual(0)
        expect(fit.lastBottom, `last card inside the screen at ${size.width}×${size.height}`).toBeLessThanOrEqual(fit.height)
        expect(fit.rows, `13 cards in ${size.rows} rows at ${size.width}×${size.height}`).toBe(size.rows)
      }
      await page.screenshot({ path: test.info().outputPath(`opening-${size.width}x${size.height}.png`), fullPage: true })

      // The big card and its Reveal button fit the screen too; closing returns focus to the card.
      await hiddenCard(page, 13).click()
      const dialog = bigCard(page, 13)
      await expect(dialog).toBeVisible()
      await settled(page)
      const big = (await page.locator('.reveal-card').boundingBox())!
      const button = (await dialog.getByRole('button', { name: 'Reveal', exact: true }).boundingBox())!
      for (const [what, box] of [
        ['big card', big],
        ['Reveal button', button],
      ] as const) {
        expect(box.x, `${what} at ${size.width}×${size.height}`).toBeGreaterThanOrEqual(0)
        expect(box.y, `${what} at ${size.width}×${size.height}`).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width, `${what} at ${size.width}×${size.height}`).toBeLessThanOrEqual(size.width)
        expect(box.y + box.height, `${what} at ${size.width}×${size.height}`).toBeLessThanOrEqual(size.height)
      }
      await noHorizontalOverflow(page)
      await page.screenshot({ path: test.info().outputPath(`opening-big-${size.width}x${size.height}.png`) })
      await dialog.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(dialog).toBeHidden()
      await expect(hiddenCard(page, 13)).toBeFocused()
    }
  })
})
