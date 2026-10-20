import type { Metadata } from 'next'
import { AdminShell } from '@/components/admin/shell'

export const metadata: Metadata = { title: { default: 'Organiser console', template: '%s · StageVote organiser' } }

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <AdminShell>{children}</AdminShell>
}
