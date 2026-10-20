import type { Metadata } from 'next'
import { ParticipantsPage } from '@/components/admin/participants/participants-page'

export const metadata: Metadata = { title: 'Participants' }

export default function Page() {
  return <ParticipantsPage />
}
