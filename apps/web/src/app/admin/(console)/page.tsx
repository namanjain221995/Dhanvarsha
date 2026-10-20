import type { Metadata } from 'next'
import { LiveControl } from '@/components/admin/live/live-control'

export const metadata: Metadata = { title: 'Live control' }

export default function LiveControlPage() {
  return <LiveControl />
}
