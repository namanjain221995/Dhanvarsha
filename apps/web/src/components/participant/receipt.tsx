import type { Receipt } from '@/lib/types'
import { timeLabel } from './bits'

/**
 * The vote receipt, drawn as a ticket stub: the part of the evening a participant keeps.
 * `celebrate` adds the stamp-in and drawn check, used only right after the server
 * confirmed the vote.
 */
export function ReceiptStub({
  receipt,
  timezone,
  round,
  celebrate = false,
}: {
  receipt: Receipt
  timezone: string
  round?: number
  celebrate?: boolean
}) {
  return (
    <section
      data-receipt={receipt.ballot_id}
      aria-label={`Receipt for ${receipt.ballot_title}`}
      className={`overflow-hidden rounded-[var(--radius-card)] border border-sand bg-paper shadow-[var(--shadow-card)] ${celebrate ? 'stamp-in' : ''}`}
    >
      <div className="px-5 pb-5 pt-5">
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-success text-white">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden className={celebrate ? 'check-draw' : ''}>
              <path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <p className="text-lg font-bold text-success">Vote recorded</p>
        </div>
        <p className="mt-4 text-sm text-plum-soft">
          {round ? `Round ${round}, ` : ''}
          {receipt.event_name}
        </p>
        <p className="text-sm font-semibold text-plum-soft">{receipt.ballot_title}</p>
        <p className="type-display mt-1 text-[1.9rem] text-plum">{receipt.nominee_name}</p>
      </div>
      <div className="perforation mx-0" aria-hidden />
      <dl className="grid grid-cols-2 gap-3 bg-gold-wash/40 px-5 py-4 text-sm">
        <div>
          <dt className="text-plum-soft">Receipt</dt>
          <dd className="tabular mt-0.5 font-bold tracking-wide text-plum">{receipt.receipt_code}</dd>
        </div>
        <div>
          <dt className="text-plum-soft">Recorded</dt>
          <dd className="tabular mt-0.5 font-semibold text-plum">{timeLabel(receipt.accepted_at, timezone)}</dd>
        </div>
      </dl>
    </section>
  )
}
