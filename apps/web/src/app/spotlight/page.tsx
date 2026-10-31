import type { Metadata, Viewport } from 'next'
import { SpotlightScreen } from '@/components/participant/spotlight-screen'

// The page sets the tab title from the API once it loads (the spotlight's own title).
export const metadata: Metadata = { title: { absolute: 'Spotlight · Dhanvarsha 2026' } }

export const viewport: Viewport = { themeColor: '#050c2c' }

/**
 * The spotlight for the big screen: people revealed one at a time from the organiser's console.
 * Outside the (app) group on purpose, like /opening: there is no sign-in, and the API names only
 * people already revealed.
 */
export default function SpotlightPage() {
  return (
    <main>
      <SpotlightScreen />
    </main>
  )
}
