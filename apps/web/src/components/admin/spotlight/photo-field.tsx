'use client'

import { ImageUp, Trash2, UserRound, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { describeError } from '@/lib/admin/errors'
import { uploadMedia } from '@/lib/admin/query'
import type { AdminSpotlightImage } from '@/lib/admin/types'
import { MiniButton } from '../primitives'

/** A person's picture: the id is saved with the spotlight, the rest is shown here. */
export type PhotoRef = (AdminSpotlightImage & { id: string }) | null

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'

/**
 * The picture on a person's card. It goes up in chunks like the opening page's media, so a photo
 * straight from a phone needs no resizing; the server stores it as WebP. Remove only clears the
 * person's choice; the file stays on the server.
 */
export function PhotoField({
  photo,
  name,
  error,
  onChange,
}: {
  photo: PhotoRef
  /** Whose picture it is, for the buttons' labels. */
  name: string
  error?: string | null
  onChange: (photo: PhotoRef) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const upload = useRef<AbortController | null>(null)
  const [percent, setPercent] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const who = name.trim() || 'this person'

  // The person removed (or the editor left) in the middle of an upload: stop sending.
  useEffect(() => {
    const running = upload
    return () => running.current?.abort()
  }, [])

  const onFile = async (file: File | undefined) => {
    if (input.current) input.current.value = ''
    if (!file || upload.current) return
    if (file.size === 0) {
      setProblem('This file is empty.')
      return
    }
    if (file.type && !file.type.startsWith('image/')) {
      setProblem('Choose a picture: JPEG, PNG, WebP or GIF.')
      return
    }
    const controller = new AbortController()
    upload.current = controller
    setProblem(null)
    setPercent(0)
    try {
      const uploaded = await uploadMedia(file, {
        signal: controller.signal,
        onProgress: (sent, total) => setPercent(total > 0 ? Math.min(100, Math.floor((sent / total) * 100)) : 0),
      })
      if (uploaded.type !== 'image') {
        setProblem('That file is not a picture. Choose a JPEG, PNG, WebP or GIF.')
        return
      }
      onChange({
        id: uploaded.id,
        url: uploaded.url,
        name: uploaded.name,
        byte_size: uploaded.byte_size,
        width: uploaded.width,
        height: uploaded.height,
      })
    } catch (failure) {
      if (!controller.signal.aborted) setProblem(describeError(failure))
    } finally {
      if (upload.current === controller) upload.current = null
      setPercent(null)
    }
  }

  const message = problem ?? error
  return (
    <div className="flex items-start gap-3">
      <div className="relative h-24 w-[4.5rem] shrink-0 overflow-hidden rounded-xl border-2 border-gold/70 bg-[#0c1d5e]">
        {photo?.url ? (
          // eslint-disable-next-line @next/next/no-img-element -- re-encoded WebP from the API
          <img src={photo.url} alt="" className="absolute inset-0 size-full object-cover" style={{ objectPosition: '50% 22%' }} />
        ) : (
          <UserRound aria-hidden className="absolute left-1/2 top-1/2 size-8 -translate-x-1/2 -translate-y-1/2 text-gold/70" />
        )}
        {percent !== null ? (
          <span className="absolute inset-x-0 bottom-0 bg-black/60 py-0.5 text-center text-xs font-semibold text-white tabular">{percent}%</span>
        ) : null}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-plum">Picture</p>
        <p className="truncate text-sm text-plum-soft" title={photo?.name}>
          {percent !== null ? 'Uploading…' : photo ? photo.name || 'Uploaded picture' : 'None: the card shows the initials.'}
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {percent !== null ? (
            <MiniButton variant="quiet" onClick={() => upload.current?.abort()}>
              <X aria-hidden className="size-4" />
              Cancel
              <span className="sr-only"> the upload</span>
            </MiniButton>
          ) : (
            <>
              <MiniButton onClick={() => input.current?.click()}>
                <ImageUp aria-hidden className="size-4" />
                {photo ? 'Replace' : 'Upload'}
                <span className="sr-only"> the picture of {who}</span>
              </MiniButton>
              {photo ? (
                <MiniButton variant="quiet" onClick={() => onChange(null)}>
                  <Trash2 aria-hidden className="size-4" />
                  Remove
                  <span className="sr-only"> the picture of {who}</span>
                </MiniButton>
              ) : null}
            </>
          )}
        </div>
        {message ? (
          <p role="alert" className="mt-1 text-sm font-medium text-danger">
            {message}
          </p>
        ) : null}
      </div>
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
