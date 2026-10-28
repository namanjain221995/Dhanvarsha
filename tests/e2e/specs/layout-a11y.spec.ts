import AxeBuilder from '@axe-core/playwright'
import { type Page, expect, test } from '@playwright/test'
import { Admin } from '../lib/admin'
import { entryButton, invitedParticipant, noHorizontalOverflow, openLink, signIn } from '../lib/participants'

const WIDTHS = [320, 375, 390, 430, 768, 1440]

/** Serious and critical axe findings on the page as it is now, one line each. */
async function seriousViolations(page: Page) {
  const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  return scan.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}

test.describe('responsive layout', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name !== 'mobile-chromium', 'viewport sweep runs once'))

  test('no horizontal overflow and the submit bar never hides the last nominee', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'layout')
    const round = await admin.createEvent(
      `A deliberately long round name to test wrapping on small phones ${Date.now().toString(36)}`,
      ['Northern Lights Collective', 'Quiet Storm', 'Saffron Circuit', 'Orbit Kitchen', 'Paper Planes', 'Monsoon Club'],
    )
    await admin.open(round)
    const context = await browser.newContext({ hasTouch: true, isMobile: true })
    const page = await context.newPage()
    // Before signing in: the page without a link, then the link page waiting for its tap
    // (opening the link binds nothing). The page without a link comes first because a tab
    // keeps the last link it opened.
    for (const [name, url] of [['signin', '/'], ['enter', person.link]] as const) {
      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: width < 700 ? 740 : 900 })
        await openLink(page, url)
        await page.waitForLoadState('networkidle')
        if (name === 'enter') await expect(entryButton(page)).toBeVisible()
        await noHorizontalOverflow(page)
        await page.screenshot({ path: test.info().outputPath(`w${width}_${name}.png`), fullPage: true })
      }
    }
    await signIn(page, person.link)
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width < 700 ? 740 : 900 })
      for (const path of ['/lobby', `/event/${round}`]) {
        await page.goto(path)
        await page.waitForLoadState('networkidle')
        await noHorizontalOverflow(page)
        await page.screenshot({ path: test.info().outputPath(`w${width}${path.replaceAll('/', '_')}.png`), fullPage: true })
      }
      // Choose the last nominee and scroll to the end: it must stay above the sticky bar.
      await page.goto(`/event/${round}`)
      await page.locator('label', { hasText: 'Monsoon Club' }).click()
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
      const bar = page.getByRole('button', { name: 'Submit vote' })
      await expect(bar).toBeInViewport()
      const lastBox = await page.locator('label', { hasText: 'Monsoon Club' }).boundingBox()
      const barTop = await page.evaluate(() => {
        const el = document.querySelector('.fixed.bottom-0') as HTMLElement | null
        return el ? el.getBoundingClientRect().top : window.innerHeight
      })
      expect(lastBox!.y + lastBox!.height).toBeLessThanOrEqual(barTop + 1)
    }
    await context.close()
    await admin.dispose()
  })
})

test.describe('accessibility', () => {
  test.beforeEach(({}, info) => test.skip(info.project.name === 'mobile-webkit', 'axe runs in Chromium projects'))

  test('sign-in, lobby and ballot pages have no serious axe violations', async ({ page, browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'axe')
    const round = await admin.createEvent(`Accessible ${Date.now().toString(36)}`, ['Alder', 'Birch', 'Cedar'])
    await admin.open(round)
    // The page without a link, then the link page waiting for its tap.
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    expect(await seriousViolations(page)).toEqual([])
    await openLink(page, person.link)
    await expect(entryButton(page)).toBeEnabled()
    expect(await seriousViolations(page)).toEqual([])

    await signIn(page, person.link)
    for (const path of ['/lobby', `/event/${round}`]) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      expect(await seriousViolations(page)).toEqual([])
    }

    // The same link opened on another browser: the "already in use" page.
    const other = await browser.newContext()
    const otherPage = await other.newPage()
    await openLink(otherPage, person.link)
    await expect(otherPage.getByText(/already in use/).first()).toBeVisible()
    expect(await seriousViolations(otherPage)).toEqual([])
    await other.close()
    await admin.dispose()
  })

  test('keyboard-only voting with visible focus', async ({ page, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'keyboard')
    const round = await admin.createEvent(`Keyboard ${Date.now().toString(36)}`, ['Dune', 'Ember', 'Fjord'])
    await admin.open(round)
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)
    await page.waitForLoadState('networkidle')
    const firstRadio = page.getByRole('radio', { name: /Dune/ })
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press('Tab')
      if (await firstRadio.evaluate((el) => el === document.activeElement)) break
    }
    await expect(firstRadio).toBeFocused()
    const ring = await page
      .locator('label', { hasText: 'Dune' })
      .evaluate((el) => getComputedStyle(el).boxShadow)
    expect(ring).not.toBe('none')
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('radio', { name: /Ember/ })).toBeChecked()
    await page.getByRole('button', { name: 'Submit vote' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Ember')
    await admin.dispose()
  })

  test('reduced motion: confirmation is static and equally clear', async ({ browser, baseURL }) => {
    const admin = await Admin.login(baseURL!)
    const person = await invitedParticipant(admin, baseURL!, 'motion')
    const round = await admin.createEvent(`Still ${Date.now().toString(36)}`, ['Glen', 'Heath'])
    await admin.open(round)
    const context = await browser.newContext({ reducedMotion: 'reduce' })
    const page = await context.newPage()
    await signIn(page, person.link)
    await page.goto(`/event/${round}`)
    await page.getByText('Heath', { exact: true }).click()
    await page.getByRole('button', { name: 'Submit vote' }).click()
    await expect(page.getByRole('region', { name: /Receipt for/ })).toContainText('Vote recorded')
    const painted = await page.evaluate(() => {
      const canvas = document.querySelector('canvas')
      const ctx = canvas?.getContext('2d')
      if (!canvas || !ctx) return 0
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      let alpha = 0
      for (let i = 3; i < data.length; i += 4) alpha += data[i]!
      return alpha
    })
    expect(painted).toBe(0)
    await context.close()
    await admin.dispose()
  })
})
