import { type Page, expect } from '@playwright/test'
import type { Admin } from './admin'
import { readInvitation } from './mailpit'

export function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.com`
}

/**
 * Import and invite a participant (through the real worker and Mailpit). The invitation
 * email carries the user ID (their email address) and the password; nothing else is needed.
 */
export async function invitedParticipant(admin: Admin, baseURL: string, tag: string) {
  const email = uniqueEmail(tag)
  await admin.importParticipants([`E2E ${tag} <${email}>`])
  const participant = await admin.findParticipant(email)
  await admin.invite(participant.id)
  const invitation = await readInvitation(email)
  void baseURL
  return { email, userId: invitation.userId, password: invitation.password, id: participant.id }
}

/** Sign in through the real form. */
export async function signIn(page: Page, userId: string, password: string) {
  await page.goto('/')
  await page.getByLabel('Email address').fill(userId)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(/\/lobby$/)
}

export async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, 'horizontal overflow in px').toBeLessThanOrEqual(0)
}
