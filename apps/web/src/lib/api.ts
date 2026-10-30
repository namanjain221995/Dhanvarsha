/**
 * Same-origin JSON client for /api/v1. Cookies carry the session (HTTP-only); the CSRF
 * token lives only in memory and is attached to state-changing requests.
 */

export type ErrorBody = {
  error?: { code?: string; message?: string; request_id?: string; details?: Record<string, unknown> }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId?: string

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId?: string) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }

  /** True when the request may not have reached the server or its answer was lost. */
  get isNetwork(): boolean {
    return this.status === 0
  }
}

type Scope = 'participant' | 'admin'
const csrf: Record<Scope, string | null> = { participant: null, admin: null }

export function setCsrfToken(scope: Scope, token: string | null) {
  csrf[scope] = token
}

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  form?: FormData
  /** Sent as it is (application/octet-stream), e.g. one chunk of a large upload. */
  raw?: Blob
  timeoutMs?: number
  signal?: AbortSignal
  idempotencyKey?: string
  query?: Record<string, string | number | boolean | undefined | null>
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `/api/v1${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

async function request<T>(scope: Scope, path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), options.timeoutMs ?? 15000)
  const onAbort = () => controller.abort(options.signal?.reason)
  options.signal?.addEventListener('abort', onAbort, { once: true })

  const headers: Record<string, string> = { Accept: 'application/json' }
  let body: BodyInit | undefined
  if (options.form) {
    body = options.form
  } else if (options.raw) {
    headers['Content-Type'] = 'application/octet-stream'
    body = options.raw
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  if (method !== 'GET' && csrf[scope]) headers['X-CSRF-Token'] = csrf[scope] as string
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    })
  } catch (error) {
    const timedOut = controller.signal.reason instanceof DOMException && controller.signal.reason.name === 'TimeoutError'
    throw new ApiError(
      0,
      timedOut ? 'TIMEOUT' : 'NETWORK',
      timedOut ? 'The server took too long to answer.' : 'Could not reach the server.',
      { cause: String(error) },
    )
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', onAbort)
  }

  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!response.ok) {
    const err = (data as ErrorBody | null)?.error
    throw new ApiError(
      response.status,
      err?.code ?? `HTTP_${response.status}`,
      err?.message ?? 'Something went wrong. Try again.',
      err?.details ?? {},
      err?.request_id ?? response.headers.get('x-request-id') ?? undefined,
    )
  }
  return data as T
}

export const participantApi = <T>(path: string, options?: RequestOptions) => request<T>('participant', path, options)
export const adminApi = <T>(path: string, options?: RequestOptions) => request<T>('admin', path, options)

export function newIdempotencyKey(): string {
  // randomUUID needs a secure context; plain-HTTP LAN testing falls back to getRandomValues.
  const c = globalThis.crypto as Crypto & { randomUUID?: () => string }
  if (typeof c.randomUUID === 'function' && globalThis.isSecureContext) return c.randomUUID()
  const bytes = new Uint8Array(16)
  c.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
