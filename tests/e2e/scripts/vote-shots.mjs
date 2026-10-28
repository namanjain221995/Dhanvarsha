// Visual walkthrough of the voting flow (synthetic data): select, submit, receipt.
// node scripts/vote-shots.mjs --base http://localhost:3101 --storage /work/shots/participant/state.json \
//   --link 'http://localhost:3101/enter#token=…' --out /work/shots/participant --width 390 [--prefix flow]
// --link is a test participant's personal link (see shot.mjs); --storage keeps the browser that
// used it, so the same link works again in later runs.
import { existsSync } from 'node:fs'
import { chromium } from '@playwright/test'

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith('--')) acc.push([cur.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true])
    return acc
  }, []),
)
const base = args.base ?? 'http://localhost:3101'
const width = Number(args.width ?? 390)
const prefix = args.prefix ?? 'flow'
const browser = await chromium.launch()
const storage = args.storage && existsSync(args.storage) ? args.storage : undefined
const context = await browser.newContext({ storageState: storage, viewport: { width, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await context.newPage()
if (args.link) {
  const token = new URLSearchParams(new URL(String(args.link)).hash.slice(1)).get('token') ?? ''
  const r = await page.request.post(`${base}/api/v1/auth/link`, { headers: { Origin: base }, data: { token } })
  console.log('sign-in', r.status())
}
await page.goto(`${base}/lobby`, { waitUntil: 'networkidle' })
await page.getByRole('link', { name: /Vote now|Continue voting|View your vote/ }).first().click()
await page.waitForURL(/\/event\//)
await page.waitForTimeout(800)
await page.screenshot({ path: `${args.out}/${prefix}-1-detail.png` })
const options = page.getByRole('radio')
if ((await options.count()) > 0) {
  await options.nth(1).check({ force: true })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${args.out}/${prefix}-2-selected.png` })
  await page.getByRole('button', { name: 'Submit vote' }).click()
  await page.getByText('Vote recorded').first().waitFor({ timeout: 15000 })
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${args.out}/${prefix}-3-confetti.png` })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${args.out}/${prefix}-4-receipt.png`, fullPage: true })
}
await page.goto(`${base}/lobby`, { waitUntil: 'networkidle' })
await page.waitForTimeout(600)
await page.screenshot({ path: `${args.out}/${prefix}-5-lobby.png` })
if (args.storage) await context.storageState({ path: args.storage })
await browser.close()
