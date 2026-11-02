import type { Metadata, Viewport } from 'next'
import { GalleryApp } from '@/components/gallery/gallery-app'

export const metadata: Metadata = { title: { absolute: 'Dhanvarsha 2026 photos' } }

export const viewport: Viewport = { themeColor: '#050c2c' }

/**
 * The photo gallery, for anyone with the link. Outside the (app) group on purpose: there is no
 * sign-in, only a form the first time.
 */
export default function GalleryPage() {
  return <GalleryApp />
}
