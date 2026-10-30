'use client'

import { Film, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { describeError } from '@/lib/admin/errors'
import { uploadMedia } from '@/lib/admin/query'
import type { AdminOpeningMedia } from '@/lib/admin/types'
import { MiniButton } from '../primitives'

/** A card's uploaded picture or video: the id is saved with the page, the rest is shown here. */
export type MediaRef = (AdminOpeningMedia & { id: string }) | null

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime'

const megabytes = (bytes: number) => {
  const mb = bytes / (1024 * 1024)
  return mb >= 100 ? Math.round(mb).toLocaleString('en-IN') : mb.toFixed(1)
}

/** "640 KB", "12.4 MB", "1.2 GB": for the file under the preview. */
function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString('en-IN')} KB`
  if (bytes < 1024 * 1024 * 1024) return `${megabytes(bytes)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/** What the browser can tell before sending anything; the server checks the file itself. */
function precheck(file: File): string | null {
  if (file.size === 0) return 'This file is empty.'
  return null
}

/**
 * "Show on button click": a picture or video the card's button shows full screen on the
 * opening page. Large videos go up in chunks with a progress bar and can be cancelled. Remove
 * only clears the card's choice; the file stays on the server.
 */
export function MediaField({
  media,
  error,
  onChange,
}: {
  media: MediaRef
  error?: string | null
  onChange: (media: MediaRef) => void
}) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const upload = useRef<AbortController | null>(null)
  const [progress, setProgress] = useState<{ name: string; sent: number; total: number } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  // The card deleted (or the editor left) in the middle of an upload: stop sending.
  useEffect(() => {
    const running = upload
    return () => running.current?.abort()
  }, [])

  const onFile = async (file: File | undefined) => {
    if (input.current) input.current.value = ''
    if (!file || upload.current) return
    const refused = precheck(file)
    if (refused) {
      setProblem(refused)
      return
    }
    const controller = new AbortController()
    upload.current = controller
    setProblem(null)
    setProgress({ name: file.name, sent: 0, total: file.size })
    try {
      const uploaded = await uploadMedia(file, {
        signal: controller.signal,
        onProgress: (sent, total) => setProgress({ name: file.name, sent, total }),
      })
      onChange({ id: uploaded.id, url: uploaded.url, type: uploaded.type, name: uploaded.name, byte_size: uploaded.byte_size })
    } catch (failure) {
      if (!controller.signal.aborted) setProblem(describeError(failure))
    } finally {
      if (upload.current === controller) upload.current = null
      setProgress(null)
    }
  }

  const cancel = () => upload.current?.abort()

  const message = problem ?? error
  const percent = progress && progress.total > 0 ? Math.min(100, Math.floor((progress.sent / progress.total) * 100)) : 0
  return (
    <div role="group" aria-labelledby={`${id}-label`} className="min-w-0">
      <p id={`${id}-label`} className="mb-1 text-sm font-semibold text-plum">
        Show on button click <span className="font-normal text-plum-soft">(optional)</span>
      </p>
      <p id={`${id}-hint`} className="mb-2 text-sm text-plum-soft">
        A picture or video the card&apos;s button shows full screen: JPEG, PNG, WebP or GIF, or MP4 or WebM video, of any
        size. Keep this page open while a big video uploads.
      </p>

      {media && !progress ? (
        <div className="flex flex-wrap items-start gap-3">
          <div className="w-40 shrink-0 overflow-hidden rounded-xl border border-sand bg-black">
            {!media.url ? (
              <span className="block aspect-video w-full" />
            ) : media.type === 'video' ? (
              <video src={media.url} muted controls playsInline preload="metadata" className="block aspect-video w-full bg-black object-contain" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- re-encoded WebP from the API
              <img src={media.url} alt="" className="block aspect-video w-full bg-black object-contain" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-sm font-semibold text-plum">
              {media.type === 'video' ? <Film aria-hidden className="size-4 shrink-0 text-gold-deep" /> : null}
              <span className="truncate" title={media.name}>
                {media.name || 'Uploaded file'}
              </span>
            </p>
            {media.url ? (
              <p className="text-sm text-plum-soft">
                {media.type === 'video' ? 'Video' : 'Picture'}
                {media.byte_size > 0 ? `, ${fileSize(media.byte_size)}` : ''}
              </p>
            ) : null}
            <div className="mt-1 flex flex-wrap gap-1">
              <MiniButton onClick={() => input.current?.click()} aria-describedby={`${id}-hint`}>
                <Upload aria-hidden className="size-4" />
                Replace
                <span className="sr-only"> the picture or video</span>
              </MiniButton>
              <MiniButton variant="quiet" onClick={() => onChange(null)}>
                <Trash2 aria-hidden className="size-4" />
                Remove
                <span className="sr-only"> the picture or video</span>
              </MiniButton>
            </div>
          </div>
        </div>
      ) : null}

      {progress ? (
        <div className="max-w-md">
          <p className="truncate text-sm font-semibold text-plum" title={progress.name}>
            Uploading {progress.name}
          </p>
          <div
            role="progressbar"
            aria-label={`Uploading ${progress.name}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-valuetext={`${percent}%, ${megabytes(progress.sent)} of ${megabytes(progress.total)} MB`}
            className="mt-1.5 h-2 overflow-hidden rounded-full bg-sand"
          >
            <div
              className="h-full origin-left rounded-full bg-gold transition-transform duration-300"
              style={{ transform: `scaleX(${percent / 100})` }}
            />
          </div>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <p className="tabular text-sm text-plum-soft">
              {percent}% · {megabytes(progress.sent)} of {megabytes(progress.total)} MB
            </p>
            <MiniButton variant="quiet" onClick={cancel}>
              <X aria-hidden className="size-4" />
              Cancel
              <span className="sr-only"> the upload</span>
            </MiniButton>
          </div>
        </div>
      ) : null}

      {!media && !progress ? (
        <MiniButton onClick={() => input.current?.click()} aria-describedby={`${id}-hint`}>
          <Upload aria-hidden className="size-4" />
          Upload image or video
        </MiniButton>
      ) : null}

      {message ? (
        <p role="alert" className="mt-1.5 text-sm font-medium text-danger">
          {message}
        </p>
      ) : null}
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(event) => void onFile(event.target.files?.[0])}
      />
    </div>
  )
}
