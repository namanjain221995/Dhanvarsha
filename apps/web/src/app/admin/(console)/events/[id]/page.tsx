import type { Metadata } from 'next'
import { Suspense } from 'react'
import { EventEditor } from '@/components/admin/events/event-editor'

export const metadata: Metadata = { title: 'Edit event' }

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Suspense fallback={null}>
      <EventEditor key={id} eventId={id} />
    </Suspense>
  )
}
