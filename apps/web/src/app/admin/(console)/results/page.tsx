import type { Metadata } from 'next'
import { Suspense } from 'react'
import { ResultsPage } from '@/components/admin/results/results-page'

export const metadata: Metadata = { title: 'Results' }

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ResultsPage />
    </Suspense>
  )
}
