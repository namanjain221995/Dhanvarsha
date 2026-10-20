import type { Metadata } from 'next'
import { ProgrammeSettingsPage } from '@/components/admin/programme/programme-settings'

export const metadata: Metadata = { title: 'Programme settings' }

export default function Page() {
  return <ProgrammeSettingsPage />
}
