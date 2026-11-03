'use client'

import { Check, Film, FolderUp, ImagePlus, LoaderCircle, RotateCcw, Upload, X } from 'lucide-react'
import { memo, useCallback, useRef, useState, useSyncExternalStore } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { plural } from '@/lib/admin/labels'
import type { QueueItem, UploadQueue, UploadState } from '@/lib/gallery-uploads'
import { IconButton, MiniButton, Panel } from '../primitives'

/** How many finished, waiting and failed files are listed. Files being sent always are. */
const SHOWN_DONE = 24
const SHOWN_WAITING = 24
const SHOWN_FAILED = 24
/** The server answers for any limits an organiser has: the console is not told them. */
const NO_LIMITS = { image: 0, video: 0 }

// What the gallery reads (sniff in services/gallery.py). From a folder, anything else, such as a
// camera's raw files, would travel the whole way only to be refused.
const READABLE_TYPE = /^(image\/(jpeg|png|gif|webp|avif|hei[cf])|video\/(mp4|quicktime|x-msvideo|avi|3gpp2?|webm|x-matroska|x-m4v))$/
// Some browsers leave the type empty, as Chrome does for HEIC.
const READABLE_NAME = /\.(jpe?g|jpe|jfif|png|gif|webp|avif|hei[cf]|hif|mp4|m4v|mov|avi|3gp|3g2|mkv|webm)$/i

const isHidden = (name: string) => name.startsWith('.')

/** Folders still being read, what they held besides pictures and videos, and what could not be read. Like the queue, it outlives the panel. */
let intake = { reading: 0, skipped: 0, unread: 0 }
const intakeListeners = new Set<() => void>()
const getIntake = () => intake
const subscribeIntake = (listener: () => void) => {
  intakeListeners.add(listener)
  return () => {
    intakeListeners.delete(listener)
  }
}

function setIntake(next: typeof intake) {
  intake = next
  for (const listener of intakeListeners) listener()
}

type Found = { files: File[]; skipped: number; unread: number }
type Dropped = { entry: FileSystemEntry | null; file: File | null }

function sortFile(file: File, found: Found) {
  if (READABLE_TYPE.test(file.type) || READABLE_NAME.test(file.name)) found.files.push(file)
  else found.skipped += 1
}

/** A folder, all the way down. Hidden files (.DS_Store) and folders (.Trashes) go unmentioned. */
async function walk(entry: FileSystemEntry, found: Found): Promise<void> {
  if (isHidden(entry.name)) return
  try {
    if (entry.isFile) {
      sortFile(await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject)), found)
      return
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    // Each read gives the next batch (100 at a time in Chrome), and an empty one at the end.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) return
      for (const child of batch) await walk(child, found)
    }
  } catch {
    found.unread += 1 // moved or unreadable since it was dropped
  }
}

/** Loose files go as they are, and the server says if it cannot read one. Folders keep only what it can. */
async function readDrop(dropped: Dropped[]): Promise<Found> {
  const found: Found = { files: [], skipped: 0, unread: 0 }
  for (const { entry, file } of dropped) {
    if (entry?.isDirectory) await walk(entry, found)
    else if (entry && file) found.files.push(file)
    // No entry: this could be a folder in disguise, which must never be sent as an empty file.
    else if (file) sortFile(file, found)
  }
  return found
}

function readFolderInput(files: File[]): Found {
  const found: Found = { files: [], skipped: 0, unread: 0 }
  for (const file of files) {
    if (!file.webkitRelativePath.split('/').some(isHidden)) sortFile(file, found)
  }
  return found
}

function addFound(queue: UploadQueue, { files, skipped, unread }: Found) {
  if (files.length) queue.add(files, NO_LIMITS)
  if (skipped || unread) setIntake({ ...intake, skipped: intake.skipped + skipped, unread: intake.unread + unread })
}

async function addDrop(queue: UploadQueue, transfer: DataTransfer) {
  // A drop's entries can only be taken while its event lasts. What they hold can be read afterwards.
  const dropped: Dropped[] = Array.from(transfer.items)
    .filter((item) => item.kind === 'file')
    .map((item) => ({ entry: item.webkitGetAsEntry(), file: item.getAsFile() }))
  const folders = dropped.some(({ entry }) => entry?.isDirectory)
  if (folders) setIntake({ ...intake, reading: intake.reading + 1 })
  try {
    addFound(queue, await readDrop(dropped))
  } finally {
    if (folders) setIntake({ ...intake, reading: intake.reading - 1 })
  }
}

/** How many files failed for each reason, the commonest first: a long list cannot show them all. */
function failureCounts(failed: QueueItem[]): [string, number][] {
  const counts = new Map<string, number>()
  for (const item of failed) {
    const reason = item.error ?? 'This file could not be sent.'
    counts.set(reason, (counts.get(reason) ?? 0) + 1)
  }
  return [...counts].sort((first, second) => second[1] - first[1])
}

/** Many files at once from the console: the photographer's set, or whole folders. */
export function UploadPanel({ queue, pending }: { queue: UploadQueue; pending: number }) {
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot)
  const { reading, skipped, unread } = useSyncExternalStore(subscribeIntake, getIntake, getIntake)
  const chooser = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement | null>(null)
  const [over, setOver] = useState(false)

  const folderRef = useCallback((node: HTMLInputElement | null) => {
    folderInput.current = node
    node?.setAttribute('webkitdirectory', '') // no React prop, and TypeScript does not know it
  }, [])

  // The button just pressed may go away with its row: keep the keyboard in the panel.
  const keepFocus = useCallback(() => {
    window.requestAnimationFrame(() => (list.current ?? chooser.current)?.focus())
  }, [])

  const done = items.filter((item) => item.state === 'done')
  const failed = items.filter((item) => item.state === 'failed')
  const waiting = items.filter((item) => item.state === 'waiting')
  const active = items.length > done.length + failed.length
  const canRetry = failed.some((item) => item.canRetry)
  const shown = [...items.filter((item) => item.state === 'sending'), ...failed.slice(0, SHOWN_FAILED), ...waiting.slice(-SHOWN_WAITING), ...done.slice(0, SHOWN_DONE)]

  // Failed files have left the batch: the bar shows what is still to go.
  const going = items.filter((item) => item.state !== 'failed')
  const totalBytes = going.reduce((total, item) => total + item.size, 0)
  const sentBytes = going.reduce((total, item) => total + (item.state === 'done' ? item.size : item.sent), 0)
  const percent = totalBytes ? Math.min(100, Math.floor((sentBytes / totalBytes) * 100)) : 0

  // Two files can share a name (each camera starts at IMG_0001), and a screen reader must tell them apart.
  const seen = new Map<string, number>()
  const rows = shown.map((item) => {
    const count = (seen.get(item.name) ?? 0) + 1
    seen.set(item.name, count)
    return { item, label: count > 1 ? `${item.name} (${count})` : item.name }
  })

  return (
    <Panel
      id="gallery-upload"
      title="Upload photos and videos"
      description="Add the photographer's photos and videos, or any others you have. Faces are found the same way as for a guest's upload, and each file appears on the page of everyone in it."
    >
      <div
        data-over={over ? '' : undefined}
        className={`flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-4 py-6 text-center transition-colors ${over ? 'border-primary bg-primary-wash' : 'border-sand-strong bg-ivory'}`}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes('Files')) return
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
          setOver(true)
        }}
        onDragLeave={(event) => {
          // Moving onto a button inside the area is not leaving it.
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false)
        }}
        onDrop={(event) => {
          event.preventDefault()
          setOver(false)
          void addDrop(queue, event.dataTransfer)
        }}
      >
        <ImagePlus aria-hidden strokeWidth={1.5} className={`size-8 ${over ? 'text-primary' : 'text-plum-soft'}`} />
        <p className="hidden font-semibold text-plum pointer-fine:block">{over ? 'Let go to add them' : 'Drop photos, videos or whole folders here'}</p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button ref={chooser} type="button" onClick={() => filesInput.current?.click()}>
            <Upload aria-hidden className="size-4" />
            Choose photos and videos
          </Button>
          {/* A phone has no folders to choose from. */}
          <span className="hidden pointer-fine:contents">
            <Button type="button" variant="secondary" onClick={() => folderInput.current?.click()}>
              <FolderUp aria-hidden className="size-4" />
              Choose a folder
            </Button>
          </span>
        </div>
        <input
          ref={filesInput}
          type="file"
          accept="image/*,video/*"
          multiple
          hidden
          aria-label="Choose photos and videos"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? [])
            if (files.length) queue.add(files, NO_LIMITS)
            event.target.value = ''
          }}
        />
        <input
          ref={folderRef}
          type="file"
          multiple
          hidden
          aria-label="Choose a folder"
          onChange={(event) => {
            addFound(queue, readFolderInput(Array.from(event.target.files ?? [])))
            event.target.value = ''
          }}
        />
      </div>

      {active ? (
        <div className="mt-4">
          <ProgressBar label="Upload progress" percent={percent} valueText={`${percent}%, ${done.length.toLocaleString('en-IN')} of ${plural(items.length, 'file')} uploaded`} />
          <p className="mt-2 text-sm text-plum-soft">
            <strong className="font-semibold text-plum">
              {done.length.toLocaleString('en-IN')} of {items.length.toLocaleString('en-IN')} uploaded.
            </strong>{' '}
            Keep this tab open until {items.length === 1 ? 'it finishes' : 'they finish'}.
          </p>
        </div>
      ) : null}

      {/* Only the outcome is announced: a batch of thousands should not read out every file. */}
      <div role="status">
        {reading ? <p className="mt-3 text-sm text-plum-soft">Reading the folders.</p> : null}
        {!active && items.length ? (
          <Notice tone={failed.length ? 'warning' : 'success'} className="mt-4">
            {done.length ? (
              <p>
                <strong className="font-semibold">{plural(done.length, 'file')} uploaded.</strong> {done.length === 1 ? 'It appears' : 'They appear'} under Files and on each person’s page once{' '}
                {done.length === 1 ? 'it is' : 'they are'} sorted.
              </p>
            ) : null}
            {failed.length ? (
              <p>
                {plural(failed.length, 'file')} could not be sent.{failed.length > SHOWN_FAILED ? '' : ' The list says why.'}
              </p>
            ) : null}
            {failed.length > SHOWN_FAILED
              ? failureCounts(failed).map(([reason, count]) => (
                  <p key={reason}>
                    {count.toLocaleString('en-IN')} of them: {reason}
                  </p>
                ))
              : null}
          </Notice>
        ) : null}
        {skipped ? <p className="mt-3 text-sm text-plum-soft">{plural(skipped, 'other file was', 'other files were')} skipped. From a folder, only photos and videos the gallery can read are sent.</p> : null}
        {unread ? <p className="mt-3 text-sm font-medium text-danger">A folder could not be read to the end, so some files were not sent. Choose it again.</p> : null}
      </div>
      {/* This count moves with every reload, so it stays out of what is announced. */}
      {!active && done.length && pending ? <p className="mt-2 text-sm text-plum-soft">{plural(pending, 'file is', 'files are')} still being sorted.</p> : null}

      {rows.length ? (
        <ul ref={list} tabIndex={0} aria-label="Files in this upload" className="mt-3 max-h-[26rem] divide-y divide-sand overflow-y-auto rounded-2xl border border-sand bg-ivory px-3">
          {rows.map(({ item, label }) => (
            <UploadRow key={item.id} item={item} label={label} queue={queue} onGone={keepFocus} />
          ))}
        </ul>
      ) : null}
      {failed.length > SHOWN_FAILED || waiting.length > SHOWN_WAITING || done.length > SHOWN_DONE ? (
        <p className="mt-2 text-sm text-plum-soft">
          {failed.length > SHOWN_FAILED ? `${(failed.length - SHOWN_FAILED).toLocaleString('en-IN')} more could not be sent. ` : ''}
          {waiting.length > SHOWN_WAITING ? `${(waiting.length - SHOWN_WAITING).toLocaleString('en-IN')} more waiting. ` : ''}
          {done.length > SHOWN_DONE ? `${(done.length - SHOWN_DONE).toLocaleString('en-IN')} more uploaded.` : ''}
        </p>
      ) : null}

      {canRetry || (!active && !reading && (items.length > 0 || skipped > 0 || unread > 0)) ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {canRetry ? (
            <MiniButton
              onClick={() => {
                queue.retryFailed()
                keepFocus()
              }}
            >
              <RotateCcw aria-hidden className="size-4" />
              Try failed files again
            </MiniButton>
          ) : null}
          {!active && !reading ? (
            <MiniButton
              variant="quiet"
              onClick={() => {
                queue.clearFinished()
                setIntake({ ...intake, skipped: 0, unread: 0 })
                keepFocus()
              }}
            >
              Clear the list
            </MiniButton>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}

function ProgressBar({ label, percent, valueText }: { label: string; percent: number; valueText?: string }) {
  return (
    <span role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={valueText} className="block h-2 overflow-hidden rounded-full bg-sand">
      <span className="block h-full origin-left rounded-full bg-gold transition-transform duration-300" style={{ transform: `scaleX(${percent / 100})` }} />
    </span>
  )
}

const STATE_WORD: Record<UploadState, string> = { waiting: 'Waiting', sending: 'Sending', done: 'Uploaded', failed: 'Not sent' }
const STATE_TONE: Record<UploadState, string> = { waiting: 'text-plum-soft', sending: 'text-plum-soft', done: 'text-success', failed: 'text-danger' }

// Rows stay still while another file's progress moves.
const UploadRow = memo(function UploadRow({ item, label, queue, onGone }: { item: QueueItem; label: string; queue: UploadQueue; onGone: () => void }) {
  const percent = item.size ? Math.min(100, Math.floor((item.sent / item.size) * 100)) : 0
  return (
    <li className="py-2">
      <div className="flex items-center gap-3">
        <span className="relative flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-plum/[0.07] text-plum-soft">
          {item.isVideo ? <Film aria-hidden strokeWidth={1.5} className="size-5" /> : <ImagePlus aria-hidden strokeWidth={1.5} className="size-5" />}
          {item.preview ? (
            // eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen file
            <img
              src={item.preview}
              alt=""
              loading="lazy"
              decoding="async"
              className="absolute inset-0 size-full object-cover"
              onError={(event) => {
                event.currentTarget.style.display = 'none' // the file is not the picture it says it is
              }}
            />
          ) : null}
        </span>
        <span className="min-w-0 flex-1 text-sm">
          <span className="block truncate font-semibold text-plum" title={item.name}>
            {item.name}
          </span>
          {item.state === 'sending' ? (
            <span className="mt-1.5 block">
              <ProgressBar label={`Uploading ${label}`} percent={percent} />
            </span>
          ) : (
            <span className={`block ${STATE_TONE[item.state]}`}>{STATE_WORD[item.state]}</span>
          )}
        </span>
        <span className="flex shrink-0 items-center">
          {item.state === 'done' ? <Check aria-hidden className="mx-2 size-5 text-success" /> : null}
          {item.state === 'sending' ? (
            <span aria-hidden className="tabular mx-1 text-sm text-plum-soft">
              {percent === 100 ? <LoaderCircle className="size-4 animate-spin" /> : `${percent}%`}
            </span>
          ) : null}
          {item.state === 'failed' && item.canRetry ? (
            <IconButton
              label={`Try ${label} again`}
              onClick={() => {
                queue.retry(item.id)
                onGone()
              }}
            >
              <RotateCcw aria-hidden className="size-4" />
            </IconButton>
          ) : null}
          {item.state !== 'done' ? (
            <IconButton
              label={`Remove ${label} from the upload list`}
              onClick={() => {
                queue.remove(item.id)
                onGone()
              }}
            >
              <X aria-hidden className="size-4" />
            </IconButton>
          ) : null}
        </span>
      </div>
      {item.error ? <p className="mt-1 text-sm text-danger sm:pl-14">{item.error}</p> : null}
    </li>
  )
})
