import { ApiError, participantApi, type RequestOptions } from './api'
import { uploadInChunks, type ChunkedOptions } from './chunked-upload'

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

const call = <T>(path: string, options: RequestOptions) => participantApi<T>(path, options)

export function uploadToGallery(file: File, options: ChunkedOptions = {}) {
  return uploadInChunks<{ status: string; duplicate: boolean }>(call, '/gallery/uploads', file, options)
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
  const extension = item.download_url.split('.').pop() ?? ''
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
