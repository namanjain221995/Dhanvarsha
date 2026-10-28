// Capture a screenshot of a StageVote page for visual review (synthetic data only).
// Usage (inside the Playwright container, host network):
//   node scripts/shot.mjs --url http://localhost:3101/admin --out /work/shots/admin.png \
//     [--width 390] [--height 844] [--admin] [--link 'http://localhost:3101/enter#token=…'] [--full] [--wait 800]
// --link signs a participant in with their personal link, as its button does. Make one for a
// test participant of a local stack with `stagevote sign-in-link --email ADDRESS` (it replaces
// their previous link); never pass a real voter's link. A link works only in the first browser
// that uses it, so keep that browser's cookies with --storage to use the link again.
import { existsSync } from 'node:fs'
import { chromium } from '@playwright/test'

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith('--')) acc.push([cur.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : true])
    return acc
  }, []),
)
const base = new URL(args.url).origin
const width = Number(args.width ?? 390)
const height = Number(args.height ?? 844)
const browser = await chromium.launch()
const storage = args.storage && existsSync(String(args.storage)) ? String(args.storage) : undefined
const context = await browser.newContext({
  storageState: storage,
  viewport: { width, height },
  deviceScaleFactor: 2,
  isMobile: width < 700,
  hasTouch: width < 700,
  reducedMotion: args.motion === 'reduce' ? 'reduce' : 'no-preference',
})
const page = await context.newPage()
if (args.admin) {
  const res = await page.request.post(`${base}/api/v1/admin/auth/login`, {
    headers: { Origin: base },
    data: { email: process.env.ADMIN_EMAIL, password: process.env.ADMIN_PASSWORD },
  })
  if (!res.ok()) throw new Error(`admin login failed: ${res.status()} ${await res.text()}`)
}
if (args.link) {
  // The token is in the link's #fragment; the page's button posts it the same way.
  const token = new URLSearchParams(new URL(String(args.link)).hash.slice(1)).get('token') ?? ''
  const res = await page.request.post(`${base}/api/v1/auth/link`, { headers: { Origin: base }, data: { token } })
  if (!res.ok()) console.error(`participant sign-in: ${res.status()} ${await res.text()}`)
}
await page.goto(args.url, { waitUntil: 'networkidle' })
await page.waitForTimeout(Number(args.wait ?? 800))
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
await page.screenshot({ path: args.out, fullPage: Boolean(args.full) })
// --storage keeps cookies between runs so one participant session can be reused.
if (args.storage) await context.storageState({ path: String(args.storage) })
console.log(JSON.stringify({ out: args.out, width, horizontalOverflowPx: overflow }))
await browser.close()
