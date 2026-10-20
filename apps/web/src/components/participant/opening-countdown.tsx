'use client'

import { useEffect, useRef } from 'react'
import { useServerNow } from '@/lib/time'

/** Days, hours, minutes and seconds until sign-in opens; reloads the programme when it does. */
export function OpeningCountdown({
  opensAt,
  onReached,
  tone = 'light',
}: {
  opensAt: string
  onReached: () => void
  tone?: 'light' | 'stage'
}) {
  const now = useServerNow(1000)
  const remaining = Date.parse(opensAt) - now
  const fired = useRef(false)
  useEffect(() => {
    if (remaining <= 0 && !fired.current) {
      fired.current = true
      onReached()
    }
  }, [remaining, onReached])
  const total = Math.max(0, Math.ceil(remaining / 1000))
  const parts = [
    { value: Math.floor(total / 86400), label: 'days' },
    { value: Math.floor((total % 86400) / 3600), label: 'hours' },
    { value: Math.floor((total % 3600) / 60), label: 'minutes' },
    { value: total % 60, label: 'seconds' },
  ].filter((p, i) => i > 0 || p.value > 0)
  return (
    <section aria-label="Time until sign-in opens" className="mt-8">
      <div className="flex gap-2 sm:gap-3" aria-hidden>
        {parts.map((p) => (
          <div
            key={p.label}
            className={`min-w-0 flex-1 rounded-2xl px-2 py-3 text-center ${tone === 'stage' ? 'stage-glass' : 'border border-sand bg-paper'}`}
          >
            <div className={`type-display tabular text-[2.2rem] sm:text-[2.6rem] ${tone === 'stage' ? 'stage-gold-text' : 'text-plum'}`}>
              {String(p.value).padStart(2, '0')}
            </div>
            <div className={`text-xs font-medium ${tone === 'stage' ? 'text-[#a7aecf]' : 'text-plum-soft'}`}>{p.label}</div>
          </div>
        ))}
      </div>
      <p className="sr-only">
        Sign-in opens in {parts.map((p) => `${p.value} ${p.label}`).join(', ')}.
      </p>
      <p className={`mt-3 text-sm ${tone === 'stage' ? 'text-center text-[#a7aecf]' : 'text-plum-soft'}`}>
        This page opens sign-in by itself when the time comes.
      </p>
    </section>
  )
}
