import { ApiError } from './api'
import { uploadToGallery } from './gallery'

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
}

const SIMULTANEOUS = 3
/** Beyond this many pictures, showing every one would hold too much memory on a phone. */
const MAX_PREVIEWS = 60

const browserCanShow = (file: File) => /^image\/(jpeg|png|webp|gif)$/.test(file.type)

export class UploadQueue {
  private items: QueueItem[] = []
  private readonly files = new Map<string, File>()
  private readonly listeners = new Set<() => void>()
  private readonly running = new Map<string, AbortController>()
  private nextId = 1

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
      const tooBig = file.size > limit
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
      await uploadToGallery(file, { signal: controller.signal, onProgress: (sent) => this.patch(id, { sent }) })
      this.files.delete(id)
      this.patch(id, { state: 'done', sent: file.size })
    } catch (error) {
      if (!controller.signal.aborted) {
        const message = error instanceof ApiError ? error.message : 'This file could not be sent. Try again.'
        const refused = error instanceof ApiError && !error.isNetwork && error.status < 500 && error.status !== 429
        if (refused) this.files.delete(id) // sending it again cannot change the answer
        this.patch(id, { state: 'failed', error: message })
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
