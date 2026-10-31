import type { Metadata } from 'next'
import { SpotlightEditorPage } from '@/components/admin/spotlight/spotlight-editor'

export const metadata: Metadata = { title: 'Spotlight' }

export default function Page() {
  return <SpotlightEditorPage />
}
