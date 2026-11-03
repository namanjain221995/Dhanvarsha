import { ApiError, participantApi, type RequestOptions } from './api'
import { stopIfAborted, uploadInChunks, type ChunkedOptions, type UploadCall } from './chunked-upload'

/** The photo gallery's API (/gallery): no sign-in, a cookie remembers who filled in the form. */

export type GalleryProgramme = { name: string; logo_url: string | null }
export type GalleryPerson = { id: string; name: string; avatar_url: string | null }

export type GalleryMe = {
  programme: GalleryProgramme
  person: GalleryPerson | null
  /** The name to start the form with, when the visitor came from their email or is signed in. */
  suggested_name: string
  known: boolean
  max_image_bytes: number
  max_video_bytes: number
  /** Files go straight from this browser to S3. */
  direct_uploads: boolean
}

export type PersonCard = GalleryPerson & { photos: number; videos: number; is_me: boolean }
export type PeopleList = { people: PersonCard[]; total: number; pending: number }

export type GalleryItem = {
  id: string
  type: 'image' | 'video'
  /** What the browser shows: the file as it was sent, or a copy this browser can play. */
  url: string
  /** The full-quality file. */
  download_url: string
  thumb_url: string
  width: number
  height: number
  duration_ms: number
  name: string
}

export type PersonPage = { person: PersonCard; media: GalleryItem[]; total: number }

export const galleryApi = <T>(path: string, options?: RequestOptions) => participantApi<T>(`/gallery${path}`, options)

/** Where a file goes, and with which sign-in: guests use the public routes, organisers the console's. */
export type UploadTarget = { call: UploadCall; root: string }

const GUEST_UPLOADS: UploadTarget = { call: participantApi, root: '/gallery/uploads' }

type Uploaded = { status: string; duplicate: boolean }

/** S3 could not be reached from this browser at all: its network blocks it, or the bucket has no CORS rule. */
class DirectBlocked extends Error {}

const PARTS_AT_ONCE = 3
const PART_RETRIES = 3
const PART_TIMEOUT_MS = 15 * 60_000
const SIGNED_AT_ONCE = 20

/** One part to its signed link. An XHR, not fetch: only XHR reports how far an upload has come. */
function sendPart(url: string, part: Blob, signal: AbortSignal | undefined, onProgress: (sent: number) => void) {
  return new Promise<void>((resolve, reject) => {
    stopIfAborted(signal)
    const xhr = new XMLHttpRequest()
    const onAbort = () => xhr.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    const done = (finish: () => void) => {
      signal?.removeEventListener('abort', onAbort)
      finish()
    }
    xhr.open('PUT', url)
    xhr.timeout = PART_TIMEOUT_MS
    xhr.upload.onprogress = (event) => onProgress(event.loaded)
    xhr.onload = () =>
      done(() => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new ApiError(xhr.status, 'S3_REFUSED', 'The upload was refused. Try again.'))))
    const failed = () => done(() => reject(new ApiError(0, 'NETWORK', 'The connection dropped. Try again.')))
    xhr.onerror = failed
    xhr.ontimeout = failed
    xhr.onabort = () => done(() => reject(new ApiError(0, 'ABORTED', 'The upload was cancelled.')))
    xhr.send(part)
  })
}

/**
 * The file goes straight to S3 in parts, so a hundred phones never queue on the server's internet line.
 * This server only signs the links, and checks the whole file when it has arrived.
 */
async function uploadDirect(file: File, { call, root }: UploadTarget, { signal, onProgress }: ChunkedOptions): Promise<Uploaded> {
  const started = await call<{ upload_id: string; part_size: number; mode: string }>(root, {
    method: 'POST',
    body: { name: file.name.slice(0, 255) || 'upload', size: file.size, content_type: file.type || 'application/octet-stream', direct: true },
    signal,
  })
  const path = `${root}/${encodeURIComponent(started.upload_id)}`
  // S3 was turned off after this page last asked, and the server started an upload through itself.
  if (started.mode !== 's3') {
    void call(path, { method: 'DELETE' }).catch(() => {})
    throw new DirectBlocked()
  }
  const count = Math.max(1, Math.ceil(file.size / started.part_size))
  const sent = new Array<number>(count).fill(0)
  const report = () => onProgress?.(sent.reduce((total, value) => total + value, 0), file.size)
  const links = new Map<number, string>()
  let reachedS3 = false
  let next = 1

  const linkFor = async (number: number) => {
    if (!links.has(number)) {
      const numbers = Array.from({ length: Math.min(SIGNED_AT_ONCE, count - number + 1) }, (_, index) => number + index)
      const { urls } = await call<{ urls: Record<string, string> }>(`${path}/parts`, { method: 'POST', body: { numbers }, signal })
      for (const [key, value] of Object.entries(urls)) links.set(Number(key), value)
    }
    return links.get(number) as string
  }

  const sendOne = async (number: number) => {
    const part = file.slice((number - 1) * started.part_size, number * started.part_size)
    for (let attempt = 0; ; attempt += 1) {
      try {
        await sendPart(await linkFor(number), part, signal, (value) => {
          sent[number - 1] = value
          report()
        })
        reachedS3 = true
        sent[number - 1] = part.size
        report()
        return
      } catch (error) {
        sent[number - 1] = 0
        if (signal?.aborted || !(error instanceof ApiError) || !error.isNetwork) throw error
        if (!reachedS3) throw new DirectBlocked()
        if (attempt >= PART_RETRIES) throw error
        await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt))
      }
    }
  }

  const sender = async () => {
    while (next <= count) {
      const number = next
      next += 1
      await sendOne(number)
    }
  }

  try {
    onProgress?.(0, file.size)
    await Promise.all(Array.from({ length: Math.min(PARTS_AT_ONCE, count) }, sender))
    // The server checks the first bytes and joins the parts: a large file takes a moment.
    return await call<Uploaded>(`${path}/complete`, { method: 'POST', timeoutMs: 300_000, signal })
  } catch (error) {
    void call(path, { method: 'DELETE' }).catch(() => {})
    throw error
  }
}

export async function uploadToGallery(file: File, options: ChunkedOptions & { direct?: boolean; target?: UploadTarget } = {}): Promise<Uploaded> {
  const target = options.target ?? GUEST_UPLOADS
  const through = () => uploadInChunks<Uploaded>(target.call, target.root, file, options)
  if (!options.direct) return through()
  try {
    return await uploadDirect(file, target, options)
  } catch (error) {
    // Blocked from S3 here: the file comes through the server instead, slower but sure.
    if (error instanceof DirectBlocked) return through()
    throw error
  }
}

export const isEnrolRequired = (error: unknown) => error instanceof ApiError && error.code === 'GALLERY_ENROL_REQUIRED'

/** "1 photo", "24 photos and 3 videos", "No photos yet". */
export function countLabel(photos: number, videos: number): string {
  const parts = [
    photos ? `${photos.toLocaleString('en-IN')} ${photos === 1 ? 'photo' : 'photos'}` : '',
    videos ? `${videos.toLocaleString('en-IN')} ${videos === 1 ? 'video' : 'videos'}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' and ') : 'No photos yet'
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = String(seconds % 60).padStart(2, '0')
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`
}

/** The name to save a file under: the name it was uploaded with and the type it has now. */
export function downloadName(item: GalleryItem): string {
  // The link may be signed, with a long query after the file's name.
  const extension = item.download_url.split('?')[0]?.split('.').pop() ?? ''
  const base = (item.name || 'dhanvarsha').replace(/\.[^.]+$/, '') || 'dhanvarsha'
  return extension ? `${base}.${extension}` : base
}

const SELFIE_EDGE = 1600

/**
 * The selfie made small enough to send quickly, upright, as a JPEG. A format this browser cannot
 * read (HEIC in Chrome) goes as it is: the server reads it.
 */
export async function prepareSelfie(file: File): Promise<File> {
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, SELFIE_EDGE / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
    return blob ? new File([blob], 'selfie.jpg', { type: 'image/jpeg' }) : file
  } catch {
    return file
  }
}
