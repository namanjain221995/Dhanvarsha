import type { Metadata } from 'next'
import { EventsListPage } from '@/components/admin/events/events-list'

export const metadata: Metadata = { title: 'Events' }

export default function Page() {
  return <EventsListPage />
}
