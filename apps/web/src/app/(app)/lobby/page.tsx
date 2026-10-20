import type { Metadata } from 'next'
import { Lobby } from '@/components/participant/lobby'

export const metadata: Metadata = { title: 'Programme' }

export default function LobbyPage() {
  return <Lobby />
}
