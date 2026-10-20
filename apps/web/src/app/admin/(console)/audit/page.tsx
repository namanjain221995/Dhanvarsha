import type { Metadata } from 'next'
import { AuditPage } from '@/components/admin/audit/audit-page'

export const metadata: Metadata = { title: 'Audit log' }

export default function Page() {
  return <AuditPage />
}
