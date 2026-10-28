// Load pages and report console errors / CSP violations (synthetic data only).
import { chromium } from '@playwright/test'
const base = process.argv[2] ?? 'http://localhost:3100'
const paths = process.argv.slice(3).length ? process.argv.slice(3) : ['/', '/enter', '/enter#token=example']
const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') problems.push(`${m.type()}: ${m.text()}`) })
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
for (const p of paths) {
  // A fresh load every time: /enter followed by /enter#token=… would only change the fragment.
  await page.goto('about:blank')
  await page.goto(base + p, { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const hydrated = await page.evaluate(() => !!document.querySelector('main') && document.body.innerText.length > 20)
  console.log(p, 'hydrated:', hydrated, 'title:', await page.title())
}
console.log(problems.length ? problems.join('\n') : 'no console errors')
await browser.close()
