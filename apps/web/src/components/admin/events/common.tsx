'use client'

import { AlertTriangle, Archive, Clock3, Lock, Megaphone } from 'lucide-react'
import type { AdminEvent } from '@/lib/admin/types'
import { StatusChip } from '../primitives'

export function EventStatusChip({ event }: { event: Pick<AdminEvent, 'status' | 'archived'> }) {
  if (event.archived) return <StatusChip tone="neutral" icon={<Archive aria-hidden className="size-3.5" />} label="Archived" />
  if (event.status === 'open') return <StatusChip tone="live" label="Live now" />
  if (event.status === 'closed') return <StatusChip tone="neutral" icon={<Lock aria-hidden className="size-3.5" />} label="Closed" />
  return <StatusChip tone="neutral" icon={<Clock3 aria-hidden className="size-3.5" />} label="Upcoming" />
}

/**
 * Problems that would stop the event from opening (the server checks the same thing).
 * A round with no ballots may open (no voting); typed-answer questions need nothing.
 */
export function readinessProblems(event: AdminEvent): string[] {
  if (event.status !== 'upcoming') return []
  const problems: string[] = []
  for (const ballot of event.ballots) {
    if (ballot.kind === 'mcq') {
      if (ballot.nominees.length < 2) problems.push(`Add at least two answers to “${ballot.title}”`)
    } else if (ballot.kind !== 'text' && ballot.nominees.length === 0) {
      problems.push(`Add nominees to “${ballot.title}”`)
    }
  }
  return problems
}

export function ReadinessChip({ event }: { event: AdminEvent }) {
  const problems = readinessProblems(event)
  if (!problems.length) return null
  return (
    <StatusChip
      tone="warning"
      icon={<AlertTriangle aria-hidden className="size-3.5" />}
      label="Not ready to open"
      title={problems.join('. ')}
    />
  )
}

export function PublishedChip() {
  return <StatusChip tone="success" icon={<Megaphone aria-hidden className="size-3.5" />} label="Results published" />
}

export function nomineeCount(event: AdminEvent): number {
  return event.ballots.reduce((sum, ballot) => sum + ballot.nominees.length, 0)
}
