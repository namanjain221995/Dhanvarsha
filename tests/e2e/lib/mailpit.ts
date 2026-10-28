import { expect, request } from '@playwright/test'

/** Reads the local Mailpit test inbox. Only synthetic @example.com recipients are used. */
const MAILPIT = process.env.MAILPIT_URL ?? 'http://localhost:8085'

export type Invitation = { link: string; token: string; text: string; html: string }

/** The newest message to `to` whose subject includes `subjectIncludes`, once at least `count` of them arrived. */
export async function waitForMessage(to: string, subjectIncludes: string, { count = 1, timeoutMs = 45_000 } = {}) {
  const api = await request.newContext({ baseURL: MAILPIT })
  try {
    let found: { ID: string }[] = []
    await expect
      .poll(
        async () => {
          const res = await api.get(`/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`)
          const data = (await res.json()) as { messages: { ID: string; Subject: string }[] }
          // Mailpit lists the newest message first.
          found = data.messages.filter((m) => m.Subject.includes(subjectIncludes))
          return found.length
        },
        { timeout: timeoutMs, intervals: [500, 1000, 2000] },
      )
      .toBeGreaterThanOrEqual(count)
    const message = await (await api.get(`/api/v1/message/${found[0]!.ID}`)).json()
    return { text: message.Text as string, html: message.HTML as string, id: found[0]!.ID }
  } finally {
    await api.dispose()
  }
}

/** The personal token in a sign-in link (`…/enter#token=…`), or '' when it has none. */
export function linkToken(link: string): string {
  return new URLSearchParams(new URL(link).hash.slice(1)).get('token') ?? ''
}

/**
 * The newest invitation to `to` (pass `count` to wait for a re-sent one). Whatever the
 * organiser's button says, the text part names the personal link on a fixed line:
 * "Enter the arena: <link>".
 */
export async function readInvitation(to: string, { count = 1 } = {}): Promise<Invitation> {
  const message = await waitForMessage(to, 'invitation', { count })
  const link = message.text.match(/Enter the arena:\s+(https?:\/\/\S+)/)?.[1]
  const token = link ? linkToken(link) : ''
  if (!link || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error(`could not parse invitation:\n${message.text}`)
  return { link, token, text: message.text, html: message.html }
}
