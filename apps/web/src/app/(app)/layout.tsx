import { ParticipantProvider } from '@/components/participant/provider'
import { ParticipantFrame } from '@/components/participant/frame'

/** Lobby and event pages share one provider, so the live connection survives navigation. */
export default function ParticipantLayout({ children }: { children: React.ReactNode }) {
  return (
    <ParticipantProvider>
      <ParticipantFrame>{children}</ParticipantFrame>
    </ParticipantProvider>
  )
}
