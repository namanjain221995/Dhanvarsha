import { type Page, expect } from '@playwright/test'
import type { Admin } from './admin'
import { readInvitation } from './mailpit'

export { linkToken } from './mailpit'

export function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.com`
}

/**
 * Import and invite a participant (through the real worker and Mailpit). The invitation
 * email carries their personal sign-in link; nothing else is needed.
 */
export async function invitedParticipant(admin: Admin, baseURL: string, tag: string) {
  const email = uniqueEmail(tag)
  await admin.importParticipants([`E2E ${tag} <${email}>`])
  const participant = await admin.findParticipant(email)
  await admin.invite(participant.id)
  const invitation = await readInvitation(email)
  expect(new URL(invitation.link).origin).toBe(new URL(baseURL).origin)
  return { email, link: invitation.link, id: participant.id }
}

/** The one button of the link page: "Enter the arena" in the gold look, "Sign in" in the classic one. */
export function entryButton(page: Page) {
  return page.getByRole('button', { name: /^(Enter the arena|Sign in)$/ })
}

/**
 * Open an emailed link the way a tap in the mail app does: always a fresh page load. (From
 * /enter, going to /enter#token=… in the same tab would only change the fragment.)
 */
export async function openLink(page: Page, link: string) {
  if (page.url() !== 'about:blank') await page.goto('about:blank')
  await page.goto(link)
}

/** Sign in like a voter: open the emailed link and tap the button. */
export async function signIn(page: Page, link: string) {
  await openLink(page, link)
  const enter = entryButton(page)
  await expect(enter).toBeEnabled()
  await enter.click()
  await expect(page).toHaveURL(/\/lobby$/)
}

export async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, 'horizontal overflow in px').toBeLessThanOrEqual(0)
}
