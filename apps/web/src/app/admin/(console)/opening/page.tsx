import type { Metadata } from 'next'
import { OpeningEditorPage } from '@/components/admin/opening/opening-editor'

export const metadata: Metadata = { title: 'Opening page' }

export default function Page() {
  return <OpeningEditorPage />
}
