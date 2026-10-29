'use client'

import {
  ListOrdered,
  LoaderCircle,
  LogOut,
  Mail,
  Menu,
  MonitorPlay,
  RadioTower,
  RefreshCw,
  ScrollText,
  SlidersHorizontal,
  Trophy,
  Users,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { Notice } from '@/components/ui/misc'
import { adminApi, setCsrfToken } from '@/lib/api'
import { beginSignOut, cancelSignOut, describeError } from '@/lib/admin/errors'
import { AdminSessionProvider, useAdminSession } from '@/lib/admin/session'
import type { LiveStatus } from '@/lib/live'
import { IconButton, MiniButton, PageSkeleton } from './primitives'

const iconClass = 'size-[1.15rem] shrink-0'

const NAV: { href: string; label: string; icon: React.ReactNode; exact?: boolean }[] = [
  { href: '/admin', label: 'Live control', icon: <RadioTower aria-hidden className={iconClass} />, exact: true },
  { href: '/admin/events', label: 'Events', icon: <ListOrdered aria-hidden className={iconClass} /> },
  { href: '/admin/participants', label: 'Participants', icon: <Users aria-hidden className={iconClass} /> },
  { href: '/admin/results', label: 'Results', icon: <Trophy aria-hidden className={iconClass} /> },
  { href: '/admin/email', label: 'Email', icon: <Mail aria-hidden className={iconClass} /> },
  { href: '/admin/programme', label: 'Programme settings', icon: <SlidersHorizontal aria-hidden className={iconClass} /> },
  { href: '/admin/opening', label: 'Opening page', icon: <MonitorPlay aria-hidden className={iconClass} /> },
  { href: '/admin/audit', label: 'Audit log', icon: <ScrollText aria-hidden className={iconClass} /> },
]

export function AdminShell({ children }: { children: React.ReactNode }) {
  return (
    <AdminSessionProvider renderPending={(state) => <ShellPending error={state.error} retry={state.retry} />}>
      <ConsoleFrame>{children}</ConsoleFrame>
    </AdminSessionProvider>
  )
}

function ShellPending({ error, retry }: { error: unknown; retry: () => void }) {
  if (error) {
    return (
      <main className="mx-auto max-w-lg px-4 pt-20">
        <Notice
          tone="danger"
          role="alert"
          title="Could not open the organiser console"
          action={<MiniButton onClick={retry}>Try again</MiniButton>}
        >
          {describeError(error)}
        </Notice>
      </main>
    )
  }
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[17rem_minmax(0,1fr)]">
      <div className="hidden border-r border-sand bg-paper lg:block" />
      <div className="px-4 pt-6 sm:px-6 lg:px-10 lg:pt-9">
        <PageSkeleton />
      </div>
    </div>
  )
}

function ConsoleFrame({ children }: { children: React.ReactNode }) {
  const { me, connection } = useAdminSession()
  const pathname = usePathname()
  // The drawer remembers the page it was opened on, so navigating closes it.
  const [drawerPath, setDrawerPath] = useState<string | null>(null)
  const drawerOpen = drawerPath !== null && drawerPath === pathname
  const menuButton = useRef<HTMLButtonElement>(null)

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[17rem_minmax(0,1fr)]">
      <a
        href="#admin-main"
        className="sr-only z-50 rounded-full bg-plum px-4 py-3 font-semibold text-paper focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>

      <aside className="hidden border-r border-sand bg-paper lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col">
        <SidebarBody pathname={pathname} />
      </aside>

      <header className="safe-top sticky top-0 z-30 border-b border-sand bg-ivory/95 backdrop-blur-sm lg:hidden">
        <div className="flex h-16 items-center gap-2 px-2">
          <IconButton
            ref={menuButton}
            label="Open navigation"
            aria-expanded={drawerOpen}
            aria-controls="admin-drawer"
            onClick={() => setDrawerPath(pathname)}
            className="text-plum"
          >
            <Menu aria-hidden className="size-6" />
          </IconButton>
          <div className="min-w-0 flex-1">
            <p className="type-condensed truncate text-[1.02rem] font-bold leading-tight text-plum">{me.programme.name}</p>
            <p className="text-xs text-plum-soft">Organiser console</p>
          </div>
          <div className="shrink-0 pr-2">
            <ConnectionStatus status={connection} compact />
          </div>
        </div>
      </header>

      <NavDrawer
        open={drawerOpen}
        onClose={() => {
          setDrawerPath(null)
          menuButton.current?.focus()
        }}
      >
        <SidebarBody pathname={pathname} onNavigate={() => setDrawerPath(null)} />
      </NavDrawer>

      <main id="admin-main" tabIndex={-1} className="min-w-0 px-4 pb-24 pt-6 outline-none sm:px-6 lg:px-10 lg:pt-9">
        <div className="mx-auto w-full max-w-[78rem]">{children}</div>
      </main>
    </div>
  )
}

function SidebarBody({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { me, connection } = useAdminSession()
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-5 pb-5 pt-6">
        <p className="text-sm font-semibold text-plum-soft">StageVote organiser</p>
        <p className="type-display mt-1 break-words text-[1.5rem] text-plum">{me.programme.name}</p>
        <div aria-hidden className="mt-4 h-px bg-gradient-to-r from-gold/70 via-gold/30 to-transparent" />
      </div>
      <nav aria-label="Console" className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <ul className="space-y-1">
          {NAV.map((item) => {
            const active = item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`)
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                  className={`flex min-h-11 items-center gap-3 rounded-xl px-3 text-[0.95rem] font-semibold transition-colors duration-150 ${
                    active ? 'bg-plum text-ivory' : 'text-plum-soft hover:bg-plum/[0.06] hover:text-plum'
                  }`}
                >
                  {item.icon}
                  {item.label}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>
      <div className="space-y-3 border-t border-sand px-5 py-4">
        <ConnectionStatus status={connection} />
        <div className="min-w-0">
          <p className="text-xs text-plum-soft">Signed in as</p>
          <p className="truncate text-sm font-semibold text-plum" title={me.admin.email}>
            {me.admin.email}
          </p>
        </div>
        <SignOutButton />
      </div>
    </div>
  )
}

const CONNECTION: Record<LiveStatus, { label: string; short: string; icon: React.ReactNode; tone: string }> = {
  live: {
    label: 'Live updates on',
    short: 'Live',
    icon: <Wifi aria-hidden className="size-4" />,
    tone: 'text-success',
  },
  connecting: {
    label: 'Connecting to live updates',
    short: 'Connecting',
    icon: <LoaderCircle aria-hidden className="size-4 animate-spin" />,
    tone: 'text-plum-soft',
  },
  reconnecting: {
    label: 'Reconnecting. Refreshing every 15 seconds meanwhile',
    short: 'Reconnecting',
    icon: <RefreshCw aria-hidden className="size-4 animate-spin [animation-duration:2.4s]" />,
    tone: 'text-gold-deep',
  },
  offline: {
    label: 'Offline. Changes may not show until you reconnect',
    short: 'Offline',
    icon: <WifiOff aria-hidden className="size-4" />,
    tone: 'text-danger',
  },
}

export function ConnectionStatus({ status, compact = false }: { status: LiveStatus; compact?: boolean }) {
  const info = CONNECTION[status]
  return (
    <p role="status" className={`flex items-start gap-2 text-sm font-semibold ${info.tone}`}>
      <span className="mt-0.5">{info.icon}</span>
      {compact ? (
        <>
          <span aria-hidden>{info.short}</span>
          <span className="sr-only">{info.label}</span>
        </>
      ) : (
        <span className="min-w-0">{info.label}</span>
      )}
    </p>
  )
}

function SignOutButton() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const signOut = async () => {
    setBusy(true)
    setError(null)
    beginSignOut()
    try {
      await adminApi('/admin/auth/logout', { method: 'POST' })
      setCsrfToken('admin', null)
      window.location.replace('/admin/login?signed_out=1')
    } catch (failure) {
      cancelSignOut()
      setError(describeError(failure))
      setBusy(false)
    }
  }
  return (
    <div>
      <MiniButton busy={busy} onClick={() => void signOut()} className="w-full">
        <LogOut aria-hidden className="size-4" />
        Sign out
      </MiniButton>
      {error ? (
        <p role="alert" className="mt-2 text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/** Left-hand navigation sheet for phones, built on a modal <dialog> (focus trap, Escape). */
function NavDrawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    const onCancel = (event: Event) => {
      event.preventDefault()
      onClose()
    }
    dialog.addEventListener('cancel', onCancel)
    return () => dialog.removeEventListener('cancel', onCancel)
  }, [onClose])
  return (
    <dialog
      ref={ref}
      id="admin-drawer"
      aria-label="Navigation"
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
      className="m-0 h-dvh max-h-none w-[min(20rem,calc(100vw-3rem))] max-w-none border-r border-sand bg-paper p-0 text-plum shadow-[var(--shadow-lift)] transition-transform duration-200 ease-[var(--ease-soft)] backdrop:bg-plum/40 starting:open:-translate-x-full lg:hidden"
    >
      {open ? (
        <div className="flex h-full flex-col">
          <div className="flex justify-end px-2 pt-2">
            <IconButton label="Close navigation" onClick={onClose} className="text-plum">
              <X aria-hidden className="size-5" />
            </IconButton>
          </div>
          <div className="min-h-0 flex-1">{children}</div>
        </div>
      ) : null}
    </dialog>
  )
}
