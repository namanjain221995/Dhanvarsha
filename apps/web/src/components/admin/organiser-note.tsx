'use client'

import { NotebookPen } from 'lucide-react'

/** Most characters an organiser note may have (the API checks the same). */
export const ORGANISER_NOTE_MAX = 2000

export const ORGANISER_NOTE_HINT = 'Only organisers see this: when to open the poll and any special instructions.'

const noteText = (note: string | null | undefined): string => (note ?? '').trim()

/**
 * An event's organiser note ("When to show" and any special instructions), line breaks kept.
 * Console only: the API never sends the note to participants.
 */
export function OrganiserNote({
  note,
  eventName,
  className = '',
}: {
  note: string | null | undefined
  eventName?: string
  className?: string
}) {
  const text = noteText(note)
  if (!text) return null
  return (
    <div
      role="note"
      aria-label={eventName ? `Organiser note for ${eventName}` : 'Organiser note'}
      className={`rounded-2xl border border-dashed border-plum-faint/60 bg-plum/[0.035] px-3 py-2 text-sm ${className}`}
    >
      <p className="flex flex-wrap items-center gap-x-1.5 text-[0.8rem] font-semibold text-plum-soft">
        <NotebookPen aria-hidden className="size-3.5 shrink-0" />
        Organiser note
        <span aria-hidden>·</span>
        <span className="font-normal">Only you see this</span>
      </p>
      <p className="mt-0.5 whitespace-pre-line break-words leading-snug text-plum">{text}</p>
    </div>
  )
}

/** The note's first line in small muted text, for lists. The full note is in the editor. */
export function OrganiserNoteLine({ note, className = '' }: { note: string | null | undefined; className?: string }) {
  const text = noteText(note)
  if (!text) return null
  return (
    <p className={`flex items-start gap-1.5 text-[0.85rem] leading-snug text-plum-soft ${className}`} title={text}>
      <NotebookPen aria-hidden className="mt-[0.15rem] size-3.5 shrink-0" />
      <span className="min-w-0 break-words">
        <span className="sr-only">Organiser note: </span>
        {text.split('\n')[0]!.trim()}
      </span>
    </p>
  )
}
