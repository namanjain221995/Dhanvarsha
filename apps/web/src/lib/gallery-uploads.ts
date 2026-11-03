import { ApiError } from './api'
import { uploadToGallery, type UploadTarget } from './gallery'

/**
 * The files someone is sending to the gallery. It lives outside React, so moving between the tabs
 * never interrupts an upload, and the page reads it through useSyncExternalStore.
 */

export type UploadState = 'waiting' | 'sending' | 'done' | 'failed'

export type QueueItem = {
  id: string
  name: string
  size: number
  isVideo: boolean
  /** A small local picture to recognise the file by; none for videos and unreadable formats. */
  preview: string | null
  state: UploadState
  sent: number
  error: string | null
  /** A failed file this queue still holds, so it can be sent again. */
  canRetry: boolean
}

const SIMULTANEOUS = 3
/** A dropped connection or a server restarting: worth trying again by itself before showing an error. */
const QUIET_RETRIES = 2
const QUIET_RETRY_MS = 2000
/** Beyond this many pictures, showing every one would hold too much memory on a phone. */
const MAX_PREVIEWS = 60

const browserCanShow = (file: File) => /^image\/(jpeg|png|webp|gif)$/.test(file.type)

export class UploadQueue {
  private items: QueueItem[] = []
  private readonly files = new Map<string, File>()
  private readonly listeners = new Set<() => void>()
  private readonly running = new Map<string, AbortController>()
  private nextId = 1

  /** `direct`: files go straight to S3. `target`: where they go, the public page's routes unless it says otherwise. */
  constructor(
    private readonly direct = false,
    private readonly target?: UploadTarget,
  ) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  getSnapshot = () => this.items

  private publish() {
    for (const listener of this.listeners) listener()
  }

  private patch(id: string, change: Partial<QueueItem>) {
    this.items = this.items.map((item) => (item.id === id ? { ...item, ...change } : item))
    this.publish()
  }

  add(files: File[], limits: { image: number; video: number }) {
    const previews = this.items.filter((item) => item.preview).length
    const added = files.map((file, index): QueueItem => {
      const id = String(this.nextId++)
      const isVideo = file.type.startsWith('video/')
      const limit = isVideo ? limits.video : limits.image
      const tooBig = limit > 0 && file.size > limit // 0 is no limit
      const empty = file.size === 0
      if (!tooBig && !empty) this.files.set(id, file)
      return {
        id,
        name: file.name || (isVideo ? 'Video' : 'Photo'),
        size: file.size,
        isVideo,
        preview: browserCanShow(file) && previews + index < MAX_PREVIEWS ? URL.createObjectURL(file) : null,
        state: tooBig || empty ? 'failed' : 'waiting',
        sent: 0,
        error: empty ? 'This file is empty.' : tooBig ? `${isVideo ? 'Videos' : 'Photos'} must be ${sizeLabel(limit)} or smaller.` : null,
        canRetry: false,
      }
    })
    this.items = [...added, ...this.items]
    this.publish()
    this.pump()
  }

  retry(id: string) {
    if (this.files.has(id)) {
      this.patch(id, { state: 'waiting', sent: 0, error: null })
      this.pump()
    }
  }

  /** Every failed file still held goes back in line. */
  retryFailed() {
    this.items = this.items.map((item): QueueItem => (item.state === 'failed' && this.files.has(item.id) ? { ...item, state: 'waiting', sent: 0, error: null } : item))
    this.publish()
    this.pump()
  }

  /** Forget a file from the list. One that is still being sent stops. */
  remove(id: string) {
    this.running.get(id)?.abort()
    const item = this.items.find((entry) => entry.id === id)
    if (item?.preview) URL.revokeObjectURL(item.preview)
    this.files.delete(id)
    this.items = this.items.filter((entry) => entry.id !== id)
    this.publish()
    this.pump()
  }

  /** Forget every file that is over with, sent or failed. */
  clearFinished() {
    const over = (item: QueueItem) => item.state === 'done' || item.state === 'failed'
    for (const item of this.items.filter(over)) {
      if (item.preview) URL.revokeObjectURL(item.preview)
      this.files.delete(item.id)
    }
    this.items = this.items.filter((item) => !over(item))
    this.publish()
  }

  /** Stop for good: files being sent stop, files waiting go. Those that are over with stay listed. */
  stop() {
    const unfinished = (item: QueueItem) => item.state === 'waiting' || item.state === 'sending'
    for (const item of this.items.filter(unfinished)) {
      this.running.get(item.id)?.abort()
      if (item.preview) URL.revokeObjectURL(item.preview)
      this.files.delete(item.id)
    }
    this.items = this.items.filter((item) => !unfinished(item))
    this.publish()
  }

  /** Files not finished yet: a reason to warn before the page closes. */
  get active() {
    return this.items.some((item) => item.state === 'waiting' || item.state === 'sending')
  }

  private pump() {
    const waiting = [...this.items].reverse().filter((item) => item.state === 'waiting')
    for (const item of waiting.slice(0, Math.max(0, SIMULTANEOUS - this.running.size))) {
      void this.send(item.id)
    }
  }

  private async send(id: string) {
    const file = this.files.get(id)
    if (!file) return
    const controller = new AbortController()
    this.running.set(id, controller)
    this.patch(id, { state: 'sending', sent: 0, error: null })
    try {
      for (let attempt = 0; ; attempt += 1) {
        try {
          await uploadToGallery(file, { direct: this.direct, target: this.target, signal: controller.signal, onProgress: (sent) => this.patch(id, { sent }) })
          break
        } catch (error) {
          const passing = error instanceof ApiError && (error.isNetwork || error.status >= 500) && error.code !== 'INSUFFICIENT_STORAGE'
          if (!passing || controller.signal.aborted || attempt >= QUIET_RETRIES) throw error
          await new Promise((resolve) => setTimeout(resolve, QUIET_RETRY_MS * (attempt + 1)))
        }
      }
      this.files.delete(id)
      this.patch(id, { state: 'done', sent: file.size })
    } catch (error) {
      if (!controller.signal.aborted) {
        const message = error instanceof ApiError ? error.message : 'This file could not be sent. Try again.'
        // Signed out, an expired link or too many at once say nothing about the file itself.
        const refused = error instanceof ApiError && !error.isNetwork && error.status < 500 && ![401, 403, 429].includes(error.status)
        if (refused) this.files.delete(id) // sending it again cannot change the answer
        this.patch(id, { state: 'failed', error: message, canRetry: this.files.has(id) })
      }
    } finally {
      this.running.delete(id)
      this.pump()
    }
  }
}

export function sizeLabel(bytes: number): string {
  const gib = bytes / (1024 * 1024 * 1024)
  if (gib >= 1) return `${Number.isInteger(gib) ? gib : gib.toFixed(1)} GB`
  return `${Math.max(1, Math.round(bytes / (1024 * 1024)))} MB`
}
