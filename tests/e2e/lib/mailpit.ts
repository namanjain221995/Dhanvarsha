import { expect, request } from '@playwright/test'

/** Reads the local Mailpit test inbox. Only synthetic @example.com recipients are used. */
const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8085'

export type Invitation = { userId: string; password: string; link: string; text: string; html: string }

export async function waitForMessage(to: string, subjectIncludes: string, timeoutMs = 45_000) {
  const api = await request.newContext({ baseURL: MAILPIT })
  try {
    let found: { ID: string } | undefined
    await expect
      .poll(
        async () => {
          const res = await api.get(`/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`)
          const data = (await res.json()) as { messages: { ID: string; Subject: string }[] }
          found = data.messages.find((m) => m.Subject.includes(subjectIncludes))
          return Boolean(found)
        },
        { timeout: timeoutMs, intervals: [500, 1000, 2000] },
      )
      .toBe(true)
    const message = await (await api.get(`/api/v1/message/${found!.ID}`)).json()
    return { text: message.Text as string, html: message.HTML as string, id: found!.ID }
  } finally {
    await api.dispose()
  }
}

export async function readInvitation(to: string): Promise<Invitation> {
  const message = await waitForMessage(to, 'invitation')
  const userId = message.text.match(/User ID \(your email address\):\s+(\S+)/)?.[1]
  const password = message.text.match(/Password:\s+([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})/)?.[1]
  const link = message.text.match(/Sign in:\s+(\S+)/)?.[1]
  if (!userId || !password || !link) throw new Error(`could not parse invitation:\n${message.text}`)
  return { userId, password, link, text: message.text, html: message.html }
}
