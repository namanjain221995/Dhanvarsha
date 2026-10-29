'use client'

import { ImageUp, Trash2 } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { describeError } from '@/lib/admin/errors'
import { uploadAsset } from '@/lib/admin/query'
import type { AssetKind } from '@/lib/admin/types'
import { MiniButton } from '../primitives'

export type ImageRef = { id: string | null; url: string | null }

/**
 * A picture in the opening page's gold-ringed navy circle: the logo above the title, or a card's
 * picture in place of its icon. Uploads at once; the page saves only the id.
 */
export function CircleImageField({
  label,
  kind,
  image,
  fallback,
  fallbackText,
  onChange,
  hint,
  error,
  size = 72,
}: {
  label: string
  kind: AssetKind
  image: ImageRef
  /** What the circle shows without a picture (the built-in mark, the card's icon). */
  fallback: React.ReactNode
  /** The same in words, next to the circle. */
  fallbackText: string
  onChange: (image: ImageRef) => void
  hint?: React.ReactNode
  error?: string | null
  size?: number
}) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const onFile = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setProblem(null)
    try {
      const asset = await uploadAsset(kind, file)
      onChange({ id: asset.id, url: asset.url })
    } catch (failure) {
      setProblem(describeError(failure))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  const message = problem ?? error
  return (
    <div role="group" aria-labelledby={`${id}-label`} className="min-w-0">
      <p id={`${id}-label`} className="mb-2 text-sm font-semibold text-plum">
        {label}
      </p>
      <div className="flex items-center gap-3">
        <OpeningCircle size={size}>
          {image.url ? (
            // eslint-disable-next-line @next/next/no-img-element -- API serves pre-optimised WebP
            <img src={image.url} alt="" className="absolute inset-0 size-full object-cover" />
          ) : (
            fallback
          )}
        </OpeningCircle>
        <div className="min-w-0">
          {image.url ? null : <p className="mb-1 text-sm text-plum-soft">{fallbackText}</p>}
          <div className="flex flex-wrap gap-1">
            <MiniButton busy={busy} onClick={() => input.current?.click()} aria-describedby={hint ? `${id}-hint` : undefined}>
              <ImageUp aria-hidden className="size-4" />
              {image.url ? 'Replace' : 'Upload'}
              <span className="sr-only"> {label.toLowerCase()}</span>
            </MiniButton>
            {image.url ? (
              <MiniButton variant="quiet" disabled={busy} onClick={() => onChange({ id: null, url: null })}>
                <Trash2 aria-hidden className="size-4" />
                Remove
                <span className="sr-only"> {label.toLowerCase()}</span>
              </MiniButton>
            ) : null}
          </div>
        </div>
      </div>
      {hint ? (
        <p id={`${id}-hint`} className="mt-2 text-sm text-plum-soft">
          {hint}
        </p>
      ) : null}
      {message ? (
        <p role="alert" className="mt-1.5 text-sm font-medium text-danger">
          {message}
        </p>
      ) : null}
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(event) => void onFile(event.target.files?.[0])}
      />
    </div>
  )
}

/** The navy disc with its gold ring, as the opening page draws it (a small preview). */
export function OpeningCircle({ size, children }: { size: number; children: React.ReactNode }) {
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 rounded-full p-[2px]"
      style={{ width: size, height: size, background: 'conic-gradient(from 210deg, #fff0b8, #dba62d, #8a6410, #f4cb52, #fff0b8)' }}
    >
      <span
        className="relative flex size-full items-center justify-center overflow-hidden rounded-full text-[#f4cb52]"
        style={{ background: 'radial-gradient(circle at 50% 32%, #1d3c9e 0%, #0c1d5e 58%, #060f36 100%)' }}
      >
        {children}
      </span>
    </span>
  )
}
