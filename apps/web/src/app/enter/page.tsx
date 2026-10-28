import type { Metadata } from 'next'
import { Suspense } from 'react'
import { SignIn } from '@/components/participant/sign-in'

export const metadata: Metadata = { title: 'Sign in' }

/**
 * The button in every invitation email opens /enter#token=…. Outside the (app) group on
 * purpose: that layout expects a session, and this page is where a session starts.
 */
export default function EnterPage() {
  return (
    <main>
      <Suspense>
        <SignIn />
      </Suspense>
    </main>
  )
}
