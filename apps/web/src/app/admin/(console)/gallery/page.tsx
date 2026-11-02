import type { Metadata } from 'next'
import { GalleryConsolePage } from '@/components/admin/gallery/gallery-console'

export const metadata: Metadata = { title: 'Photo gallery' }

export default function Page() {
  return <GalleryConsolePage />
}
