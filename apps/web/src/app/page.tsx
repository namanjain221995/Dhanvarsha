import { Suspense } from 'react'
import { SignIn } from '@/components/participant/sign-in'

export default function HomePage() {
  return (
    <main>
      <Suspense>
        <SignIn />
      </Suspense>
    </main>
  )
}
