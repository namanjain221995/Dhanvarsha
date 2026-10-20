import type { Metadata } from 'next'
import { EmailPage } from '@/components/admin/email/email-page'

export const metadata: Metadata = { title: 'Email' }

export default function Page() {
  return <EmailPage />
}
