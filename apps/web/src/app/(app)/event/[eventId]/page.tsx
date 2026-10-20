import type { Metadata } from 'next'
import { EventDetailView } from '@/components/participant/event-detail'

export const metadata: Metadata = { title: 'Vote' }

export default async function EventPage({ params }: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await params
  return <EventDetailView key={eventId} eventId={eventId} />
}
