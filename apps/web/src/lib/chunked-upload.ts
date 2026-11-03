import { ApiError, type RequestOptions } from './api'

/** Network errors (or time-outs) tolerated per chunk before the upload gives up. */
const CHUNK_RETRIES = 3

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export function stopIfAborted(signal: AbortSignal | undefined) {
  if (signal?.aborted) throw new ApiError(0, 'ABORTED', 'The upload was cancelled.')
}

/** The API call to use: the organisers' console and the photo gallery sign in differently. */
export type UploadCall = <T>(path: string, options: RequestOptions) => Promise<T>

export type ChunkedOptions = { signal?: AbortSignal; onProgress?: (sent: number, total: number) => void }

/**
 * Uploads a file in chunks, so a file of any size passes the proxies' per-request limits: start,
 * then one PUT per chunk in order, then complete. A chunk that fails on the network is sent again
 * (up to three times); when the server has a different byte count than expected (an answer was
 * lost), the upload carries on from the server's count. On failure or cancel (`signal`) the server
 * drops what it received.
 */
export async function uploadInChunks<T>(call: UploadCall, root: string, file: File, { signal, onProgress }: ChunkedOptions = {}): Promise<T> {
  stopIfAborted(signal)
  const started = await call<{ upload_id: string; chunk_size: number }>(root, {
    method: 'POST',
    body: { name: file.name.slice(0, 255) || 'upload', size: file.size, content_type: file.type || 'application/octet-stream' },
    signal,
  })
  const path = `${root}/${encodeURIComponent(started.upload_id)}`
  const chunkSize = started.chunk_size > 0 ? started.chunk_size : 8 * 1024 * 1024
  try {
    let offset = 0
    let failures = 0
    onProgress?.(0, file.size)
    while (offset < file.size) {
      stopIfAborted(signal)
      let received: number
      try {
        ;({ received } = await call<{ received: number }>(path, {
          method: 'PUT',
          query: { offset },
          raw: file.slice(offset, Math.min(file.size, offset + chunkSize)),
          // 8 MiB on a slow venue uplink can take a while.
          timeoutMs: 180_000,
          signal,
        }))
      } catch (error) {
        stopIfAborted(signal)
        if (!(error instanceof ApiError)) throw error
        const known = error.details.received
        if (error.code === 'UPLOAD_OFFSET_MISMATCH' && typeof known === 'number') {
          received = known
        } else if (error.isNetwork && failures < CHUNK_RETRIES) {
          await pause(1000 * 2 ** failures)
          received = offset
        } else {
          throw error
        }
      }
      // No progress (a lost answer, a dropped connection): a few more tries, then give up.
      failures = received > offset ? 0 : failures + 1
      if (failures > CHUNK_RETRIES) throw new ApiError(0, 'UPLOAD_STALLED', 'The upload stopped making progress. Try again.')
      offset = Math.max(0, Math.min(file.size, received))
      onProgress?.(offset, file.size)
    }
    stopIfAborted(signal)
    // The server checks and moves the whole file now: a large video takes a moment.
    return await call<T>(`${path}/complete`, { method: 'POST', timeoutMs: 300_000, signal })
  } catch (error) {
    void call(path, { method: 'DELETE' }).catch(() => {})
    throw error
  }
}
