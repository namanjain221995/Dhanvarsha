import type { Metadata, Viewport } from 'next'
import { OpeningShowcase } from '@/components/participant/opening-page'

// The page sets the tab title from the API once it loads (the programme's own title and year).
export const metadata: Metadata = { title: { absolute: 'Dhanvarsha 2026 · Opening' } }

export const viewport: Viewport = { themeColor: '#050c2c' }

/**
 * The public showcase for the big screen (projector, LED wall) and anyone's phone. Outside the
 * (app) group on purpose: there is no sign-in, and nothing personal is shown.
 */
export default function OpeningPage() {
  return (
    <main>
      <OpeningShowcase />
    </main>
  )
}
