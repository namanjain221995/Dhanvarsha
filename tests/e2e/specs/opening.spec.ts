import { type Page, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { noHorizontalOverflow } from '../lib/participants'

/*
 * The opening page (/opening): the public showcase for the projector and the LED wall, and its
 * editor in the organiser console. Every test starts from the built-in defaults and the editing
 * test puts them back, so nothing here depends on another spec.
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
  logo_url: string | null
  cards: { id: string; title: string; icon: string; image_url: string | null; link: string }[]
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

  test('shows the 13 default cards with the PLAY GAME button', async ({ page }) => {
    const res = await page.request.get('/api/v1/public/opening')
    expect(res.status(), await res.text()).toBe(200)
    const data = (await res.json()) as PublicOpening
    expect(data.cards.map((card) => card.title)).toEqual(DEFAULT_CARDS)
    expect(data.button_label).toBe('PLAY GAME')

    await page.goto('/opening')
    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toContainText(data.title)
    if (data.year) await expect(heading).toContainText(data.year)
    await expect(page).toHaveTitle(`${[data.title, data.year].filter(Boolean).join(' ')} · Opening`)
    if (data.tagline) await expect(page.getByText(data.tagline.split('\n')[0]!.trim())).toBeVisible()

    await expect(cards(page)).toHaveCount(13)
    await expect(cardTitles(page)).toHaveText(DEFAULT_CARDS)
    await expect(pills(page)).toHaveText(Array(13).fill('PLAY GAME'))
    // The default cards have no links: their pills are decoration, never a dead link or a focus stop.
    await expect(page.getByRole('link')).toHaveCount(0)
    await expect(pills(page).first()).toHaveAttribute('aria-hidden', 'true')
    expect(await pills(page).first().evaluate((el) => el.tagName)).toBe('SPAN')
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
      expect(saved.opening.cards.slice(0, 2).map((card) => card.title)).toEqual([renamed, DEFAULT_CARDS[0]])

      // The public page shows the new label on every button and the new order.
      const screen = await context.newPage()
      await screen.goto('/opening')
      await expect(cardTitles(screen)).toHaveText([renamed, DEFAULT_CARDS[0]!, ...DEFAULT_CARDS.slice(2)])
      await expect(pills(screen)).toHaveText(Array(13).fill(label))

      // Reset to defaults, after a confirmation, and the page follows again.
      await page.getByRole('button', { name: 'Reset to defaults' }).click()
      const dialog = page.getByRole('dialog', { name: 'Reset the opening page?' })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: 'Reset to defaults' }).click()
      await expect(dialog).toBeHidden()
      await expect(page.locator('[aria-live]').filter({ hasText: 'back to the defaults' }).first()).toBeVisible()
      await expect(cardGroup(page, 1).getByLabel('Title', { exact: true })).toHaveValue(DEFAULT_CARDS[0]!)
      await expect(page.getByLabel('Button text')).toHaveValue('PLAY GAME')

      await screen.reload()
      await expect(cardTitles(screen)).toHaveText(DEFAULT_CARDS)
      await expect(pills(screen)).toHaveText(Array(13).fill('PLAY GAME'))
    } finally {
      await resetToDefaults(baseURL!)
    }
  })

  test('fits a phone, a laptop, the projector and the LED wall', async ({ page }) => {
    // 1920 × 872 is the 2.2:1 LED wall (22 × 10 ft). The two big screens show everything at once.
    const sizes = [
      { width: 390, height: 844, oneScreen: false },
      { width: 1366, height: 768, oneScreen: false },
      { width: 1920, height: 1080, oneScreen: true },
      { width: 1920, height: 872, oneScreen: true },
    ]
    for (const size of sizes) {
      await page.setViewportSize({ width: size.width, height: size.height })
      await page.goto('/opening')
      await expect(cards(page)).toHaveCount(13)
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
        expect(fit.rows, `13 cards in two rows at ${size.width}×${size.height}`).toBe(2)
      }
      await page.screenshot({ path: test.info().outputPath(`opening-${size.width}x${size.height}.png`), fullPage: true })
    }
  })
})
