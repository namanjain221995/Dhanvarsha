import { ApiError } from '@/lib/api'

/** Copy for error codes where the console says more than the API message. */
const OVERRIDES: Record<string, string> = {
  STALE_STATE: 'Another organiser or the schedule changed the live state. Review and try again.',
  STALE_SETTINGS: 'Someone else changed these settings. Load their version, then make your change again.',
  STALE_EVENT: 'This event changed since you opened it. Load the latest version, then make your change again.',
  NETWORK: 'Could not reach the server. Check your connection and try again.',
  TIMEOUT: 'The server took too long to answer. Check the current state before trying again.',
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function errorCode(error: unknown): string | null {
  return error instanceof ApiError ? error.code : null
}

/** One plain sentence (or two) for any thrown value. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const base = OVERRIDES[error.code] ?? error.message ?? 'Something went wrong. Try again.'
    if (error.status >= 500 && error.requestId) return `${base} Reference: ${error.requestId.slice(0, 8)}.`
    return base
  }
  if (error instanceof Error && error.message) return error.message
  return 'Something went wrong. Try again.'
}

/**
 * The API's own validation errors (`details.fields`) mapped to form fields. Pydantic
 * messages are technical; they are shortened to plain sentences where we can.
 */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const fields = (error.details?.fields ?? []) as { field?: string; message?: string }[]
  const out: Record<string, string> = {}
  if (!Array.isArray(fields)) return out
  for (const item of fields) {
    if (!item?.field) continue
    const key = item.field.split('.').pop() ?? item.field
    out[key] = plainValidation(item.message ?? 'Check this value.')
  }
  return out
}

function plainValidation(message: string): string {
  const tooLong = /at most (\d+) character/.exec(message)
  if (tooLong) return `Use ${tooLong[1]} characters or fewer.`
  if (/at least 1 character/.test(message)) return 'This cannot be empty.'
  if (/valid datetime|timezone/i.test(message)) return 'Enter a complete date and time.'
  if (/Input should be/.test(message)) return 'Choose one of the allowed values.'
  if (/should match pattern/.test(message)) return 'Remove line breaks and special characters.'
  return message.endsWith('.') ? message : `${message}.`
}

/** Unauthenticated anywhere in the console: go to sign-in and come back afterwards. */
let redirecting = false
/**
 * A deliberate sign-out also revokes this tab's own live connection; suppress the
 * "session ended" redirect so the sign-out confirmation page is shown instead.
 */
export function beginSignOut() {
  redirecting = true
}

export function cancelSignOut() {
  redirecting = false
}

export function goToLogin() {
  if (typeof window === 'undefined' || redirecting) return
  redirecting = true
  const next = `${window.location.pathname}${window.location.search}`
  const target = next.startsWith('/admin') && !next.startsWith('/admin/login') ? next : '/admin'
  window.location.replace(`/admin/login?next=${encodeURIComponent(target)}`)
}

export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
