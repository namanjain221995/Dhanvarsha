'use client'

import { Clock3, Drama, ScrollText } from 'lucide-react'
import { Dialog } from '@/components/ui/dialog'
import { Avatar } from '@/components/ui/misc'
import { useAdminQuery } from '@/lib/admin/query'
import type { EventPreview, ProgrammeSettings } from '@/lib/admin/types'
import { formatWhen } from '@/lib/admin/zoned'
import { LoadError, SkeletonBlock } from '../primitives'

/**
 * Read-only rendering of `/admin/events/{id}/preview`: roughly what a participant sees on
 * a phone while the event is live. Buttons are inert.
 */
export function EventPreviewDialog({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const preview = useAdminQuery<EventPreview>(`/admin/events/${eventId}/preview`)
  const programme = useAdminQuery<ProgrammeSettings>('/admin/programme')
  const data = preview.data
  const accent = programme.data?.accent ?? 'violet'

  return (
    <Dialog
      open
      wide
      onClose={onClose}
      title="Participant preview"
      description="How this event looks on a phone while it is live. Nothing here can be submitted."
    >
      {preview.error && !data ? (
        <LoadError error={preview.error} onRetry={() => void preview.reload()} what="the preview" />
      ) : !data ? (
        <div className="mx-auto w-full max-w-[22rem] space-y-3" aria-busy="true">
          <SkeletonBlock className="h-8 w-40" />
          <SkeletonBlock className="h-40 w-full rounded-[1.5rem]" />
          <SkeletonBlock className="h-20 w-full rounded-[1.5rem]" />
        </div>
      ) : (
        <div
          data-accent={accent === 'violet' ? undefined : accent}
          className="mx-auto w-full max-w-[22rem] rounded-[2.25rem] border-[6px] border-plum/85 bg-ivory p-1.5 shadow-[var(--shadow-lift)]"
        >
          <div className="max-h-[60vh] overflow-y-auto rounded-[1.75rem] bg-ivory px-3.5 pb-5 pt-4">
            <p className="text-center text-xs font-semibold text-plum-soft">{programme.data?.name ?? 'Programme'}</p>
            <div className="mt-3 rounded-[var(--radius-card)] border border-sand bg-paper p-4 shadow-[var(--shadow-card)]">
              <div className="flex items-center gap-2">
                <span className="inline-flex min-h-7 items-center gap-1.5 rounded-full bg-primary-wash px-2.5 text-[0.8rem] font-semibold text-primary">
                  <span aria-hidden className="live-lamp" />
                  Live now
                </span>
                {data.event.round ? (
                  <span className="text-xs font-semibold text-plum-soft">
                    Round {data.event.round} of {data.event_count}
                  </span>
                ) : null}
              </div>
              <div className="mt-3 flex items-center gap-3">
                <Avatar src={data.event.logo_url} name={data.event.name} size={52} rounded="xl" />
                <p className="type-display min-w-0 break-words text-[1.7rem] text-plum">{data.event.name}</p>
              </div>
              {data.event.description ? <p className="mt-3 text-[0.92rem] text-plum-soft">{data.event.description}</p> : null}
              <p className="mt-3 flex items-center gap-1.5 text-sm font-semibold text-plum">
                <Clock3 aria-hidden className="size-4" />
                {data.event.voting_deadline_at
                  ? `Voting closes ${formatWhen(data.event.voting_deadline_at, data.timezone)}`
                  : 'Voting closes at the deadline set when it opens'}
              </p>
              {data.event.rules ? (
                <div className="mt-3 rounded-2xl bg-ivory px-3 py-2.5 text-sm text-plum-soft">
                  <p className="flex items-center gap-1.5 font-semibold text-plum">
                    <ScrollText aria-hidden className="size-4" />
                    Rules
                  </p>
                  <p className="mt-1">{data.event.rules}</p>
                </div>
              ) : null}
            </div>

            {data.ballots.length === 0 ? (
              <div className="mt-5 flex items-center gap-3 rounded-[var(--radius-card)] border border-sand bg-paper p-4">
                <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-full bg-gold-wash text-gold-deep">
                  <Drama className="size-5" />
                </span>
                <p className="type-condensed text-[1.1rem] font-bold leading-snug text-plum">No voting in this round. Enjoy the show!</p>
              </div>
            ) : null}

            {data.ballots.map((ballot) => (
              <div key={ballot.id} className="mt-5">
                <p className="type-condensed text-[1.15rem] font-bold text-plum">{ballot.title}</p>
                {ballot.description ? <p className="text-sm text-plum-soft">{ballot.description}</p> : null}
                <p className="text-xs text-plum-soft">
                  {ballot.kind === 'text'
                    ? 'Type your answer. It is final once submitted.'
                    : ballot.kind === 'mcq'
                      ? 'Choose one answer.'
                      : `Choose one ${ballot.kind === 'team' ? 'team' : 'person'}.`}
                </p>
                {ballot.kind === 'text' ? (
                  <div className="mt-2 rounded-2xl border-2 border-sand bg-paper px-3 pb-2 pt-2.5">
                    <p className="min-h-16 text-sm text-plum-faint">Type your answer here</p>
                    <p className="tabular text-right text-xs text-plum-soft">0/500</p>
                  </div>
                ) : ballot.kind === 'mcq' && ballot.nominees.length ? (
                  <ul className="mt-2 space-y-2">
                    {ballot.nominees.map((option, index) => (
                      <li key={option.id} className="flex items-center gap-3 rounded-2xl border border-sand bg-paper p-3">
                        <span
                          aria-hidden
                          className="type-condensed flex size-10 shrink-0 items-center justify-center rounded-xl border-2 border-sand-strong bg-ivory font-bold text-plum"
                        >
                          {index < 26 ? String.fromCharCode(65 + index) : index + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="break-words font-semibold leading-snug text-plum">{option.name}</p>
                          {option.description ? <p className="line-clamp-2 text-xs text-plum-soft">{option.description}</p> : null}
                        </div>
                        <span aria-hidden className="size-5 shrink-0 rounded-full border-2 border-plum-faint" />
                      </li>
                    ))}
                  </ul>
                ) : ballot.nominees.length === 0 ? (
                  <p className="mt-2 rounded-2xl border border-dashed border-gold/60 bg-gold-wash/60 px-3 py-2 text-sm text-plum">
                    {ballot.kind === 'mcq' ? 'No answer options yet.' : 'No nominees yet.'}
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {ballot.nominees.map((nominee) => (
                      <li key={nominee.id} className="flex items-center gap-3 rounded-2xl border border-sand bg-paper p-3">
                        <Avatar src={nominee.photo_url} name={nominee.name} size={44} />
                        <div className="min-w-0 flex-1">
                          <p className="break-words font-semibold leading-snug text-plum">{nominee.name}</p>
                          {nominee.affiliation ? <p className="truncate text-xs text-plum-soft">{nominee.affiliation}</p> : null}
                          {ballot.kind === 'team' && nominee.members.length ? (
                            <p className="text-xs text-plum">
                              <span className="sr-only">Team members: </span>
                              {nominee.members.join(', ')}
                            </p>
                          ) : null}
                          {nominee.description ? <p className="line-clamp-2 text-xs text-plum-soft">{nominee.description}</p> : null}
                        </div>
                        <span aria-hidden className="size-5 shrink-0 rounded-full border-2 border-plum-faint" />
                      </li>
                    ))}
                  </ul>
                )}
                <div aria-hidden className="mt-3 flex min-h-12 items-center justify-center rounded-full bg-primary/40 font-semibold text-white">
                  {ballot.kind === 'mcq' || ballot.kind === 'text' ? 'Submit answer' : 'Submit vote'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Dialog>
  )
}
