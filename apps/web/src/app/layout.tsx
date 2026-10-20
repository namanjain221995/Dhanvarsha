import type { Metadata, Viewport } from 'next'
import localFont from 'next/font/local'
import { connection } from 'next/server'
import './globals.css'

// Archivo (SIL Open Font License), self-hosted: the app never loads a remote font.
const archivo = localFont({
  src: './fonts/archivo-latin-wdth-normal.woff2',
  variable: '--font-archivo',
  weight: '100 900',
  display: 'swap',
  declarations: [{ prop: 'font-stretch', value: '62% 125%' }],
})

// Cinzel (SIL Open Font License), self-hosted: the classical display face of the stage
// sign-in in the gold look. Its lowercase letters are small capitals.
const cinzel = localFont({
  src: './fonts/cinzel-latin-wght-normal.woff2',
  variable: '--font-cinzel',
  weight: '400 900',
  display: 'swap',
})

export const metadata: Metadata = {
  title: { default: 'StageVote', template: '%s · StageVote' },
  description: 'Live voting for your programme.',
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  interactiveWidget: 'resizes-content',
  themeColor: '#fbf7ef',
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Every page renders per request so the CSP script nonce from proxy.ts applies.
  await connection()
  return (
    <html lang="en" className={`${archivo.variable} ${cinzel.variable}`}>
      <body>{children}</body>
    </html>
  )
}
