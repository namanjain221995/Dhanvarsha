'use client'

import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Circle, Info, LoaderCircle, X, XCircle } from 'lucide-react'
import { forwardRef, useCallback, useId, useRef, useState } from 'react'
import { Notice } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'

// ---- buttons ------------------------------------------------------------------

type MiniVariant = 'secondary' | 'quiet' | 'danger' | 'primary' | 'link'
const miniVariants: Record<MiniVariant, string> = {
  secondary:
    'border border-plum-faint/60 bg-paper text-plum hover:border-plum-soft hover:bg-white disabled:border-sand disabled:text-plum-faint',
  quiet: 'text-plum-soft hover:bg-plum/5 hover:text-plum disabled:text-plum-faint',
  danger: 'text-danger hover:bg-danger-wash disabled:text-plum-faint',
  primary: 'bg-primary text-white hover:bg-primary-deep disabled:bg-plum-faint/40',
  link: 'text-primary hover:bg-primary-wash hover:text-primary-deep disabled:text-plum-faint',
}

/** Compact button for dense rows and toolbars. Still a 44px touch target. */
export const MiniButton = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: MiniVariant; busy?: boolean }
>(function MiniButton({ variant = 'secondary', busy = false, className = '', children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex min-h-11 select-none items-center justify-center gap-1.5 rounded-full px-3.5 text-sm font-semibold transition-colors duration-150 disabled:cursor-not-allowed ${miniVariants[variant]} ${className}`}
      {...rest}
    >
      {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {children}
    </button>
  )
})

/** Square 44px icon button; `label` is required because the icon has no text. */
export const IconButton = forwardRef<
  HTMLButtonElement,
  Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & { label: string }
>(function IconButton({ label, className = '', children, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={`inline-flex size-11 shrink-0 items-center justify-center rounded-full text-plum-soft transition-colors hover:bg-plum/5 hover:text-plum disabled:cursor-not-allowed disabled:text-plum-faint/60 disabled:hover:bg-transparent ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
})

// ---- page structure -----------------------------------------------------------

export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: string
  description?: React.ReactNode
  actions?: React.ReactNode
  meta?: React.ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="type-display text-[2rem] text-plum sm:text-[2.5rem]">{title}</h1>
        {description ? <p className="mt-1.5 max-w-2xl text-[0.95rem] text-plum-soft">{description}</p> : null}
        {meta ? <div className="mt-2 text-sm text-plum-soft">{meta}</div> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = '',
  headingLevel = 2,
  id,
  tone = 'paper',
}: {
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  children?: React.ReactNode
  className?: string
  headingLevel?: 2 | 3
  id?: string
  tone?: 'paper' | 'quiet'
}) {
  const autoId = useId()
  const headingId = id ?? `panel-${autoId}`
  const Heading = headingLevel === 2 ? 'h2' : 'h3'
  return (
    <section
      aria-labelledby={headingId}
      className={`min-w-0 rounded-[var(--radius-card)] border border-sand ${tone === 'paper' ? 'bg-paper' : 'bg-ivory'} ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 pt-4 sm:px-6 sm:pt-5">
        <div className="min-w-0">
          <Heading id={headingId} className="text-[1.05rem] font-bold leading-snug text-plum">
            {title}
          </Heading>
          {description ? <div className="mt-0.5 text-sm text-plum-soft">{description}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="px-4 pb-4 pt-3 sm:px-6 sm:pb-6">{children}</div>
    </section>
  )
}

// ---- status -------------------------------------------------------------------

export type Tone = 'live' | 'success' | 'warning' | 'danger' | 'neutral' | 'info'

const toneClass: Record<Tone, string> = {
  live: 'bg-gold-wash text-gold-deep ring-1 ring-inset ring-gold/40',
  success: 'bg-success-wash text-success',
  warning: 'bg-gold-wash text-gold-deep',
  danger: 'bg-danger-wash text-danger',
  neutral: 'bg-plum/[0.06] text-plum-soft',
  info: 'bg-plum/[0.06] text-plum',
}

const toneIcon: Record<Tone, React.ReactNode> = {
  live: <span aria-hidden className="live-lamp" />,
  success: <CheckCircle2 aria-hidden className="size-3.5" />,
  warning: <AlertTriangle aria-hidden className="size-3.5" />,
  danger: <XCircle aria-hidden className="size-3.5" />,
  neutral: <Circle aria-hidden className="size-3.5" />,
  info: <Info aria-hidden className="size-3.5" />,
}

/** Status as words plus an icon plus colour — never colour alone. */
export function StatusChip({
  tone,
  label,
  icon,
  className = '',
  title,
}: {
  tone: Tone
  label: React.ReactNode
  icon?: React.ReactNode
  className?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={`inline-flex min-h-7 max-w-full shrink-0 items-center gap-1.5 rounded-full px-2.5 text-[0.8rem] font-semibold leading-tight ${toneClass[tone]} ${className}`}
    >
      {icon ?? toneIcon[tone]}
      <span className="min-w-0 truncate">{label}</span>
    </span>
  )
}

// ---- data display -------------------------------------------------------------

/** Horizontal bar. Purely visual: the numbers it shows are always written next to it. */
export function Bar({
  value,
  max,
  tone = 'ink',
  size = 'md',
}: {
  value: number
  max: number
  tone?: 'ink' | 'soft' | 'gold' | 'success'
  size?: 'sm' | 'md' | 'lg'
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const fill = { ink: 'bg-plum', soft: 'bg-plum/35', gold: 'bg-gold', success: 'bg-success' }[tone]
  const height = { sm: 'h-1.5', md: 'h-2.5', lg: 'h-3.5' }[size]
  return (
    <div aria-hidden className={`${height} w-full overflow-hidden rounded-full bg-plum/[0.07]`}>
      <div
        className={`h-full rounded-full ${fill} transition-[width] duration-500 ease-[var(--ease-soft)]`}
        style={{ width: `${pct}%`, minWidth: value > 0 ? '0.375rem' : undefined }}
      />
    </div>
  )
}

export function KeyValue({ items, className = '' }: { items: [React.ReactNode, React.ReactNode][]; className?: string }) {
  return (
    <dl className={`grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2 text-[0.92rem] ${className}`}>
      {items.map(([key, value], index) => (
        <div key={index} className="contents">
          <dt className="text-plum-soft">{key}</dt>
          <dd className="min-w-0 break-words font-medium text-plum">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: React.ReactNode
  title: string
  children?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-2xl border border-dashed border-sand-strong bg-ivory px-5 py-6">
      {icon ? <span className="text-plum-soft">{icon}</span> : null}
      <p className="font-semibold text-plum">{title}</p>
      {children ? <div className="max-w-xl text-sm text-plum-soft">{children}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

export function SkeletonBlock({ className = '' }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />
}

export function PageSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-live="polite" className="space-y-4">
      <span className="sr-only">Loading…</span>
      <SkeletonBlock className="h-10 w-64" />
      <SkeletonBlock className="h-4 w-80 max-w-full" />
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonBlock key={i} className="h-36 w-full rounded-[var(--radius-card)]" />
      ))}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-plum-soft">
      <LoaderCircle aria-hidden className="size-4 animate-spin" />
      {label ? <span>{label}</span> : <span className="sr-only">Loading</span>}
    </span>
  )
}

/** Load failure with a retry, for whole panels or pages. */
export function LoadError({ error, onRetry, what = 'this page' }: { error: unknown; onRetry: () => void; what?: string }) {
  return (
    <Notice
      tone="danger"
      role="alert"
      title={`Could not load ${what}`}
      action={
        <MiniButton onClick={onRetry}>Try again</MiniButton>
      }
    >
      {describeError(error)}
    </Notice>
  )
}

// ---- action feedback ----------------------------------------------------------

export type Flash = { tone: 'success' | 'info' | 'warning' | 'danger'; title?: string; message: React.ReactNode }

/** Result of the last action. The region stays mounted so screen readers announce changes. */
export function useFlash() {
  const [flash, setFlash] = useState<Flash | null>(null)
  const show = useCallback((next: Flash) => setFlash(next), [])
  const clear = useCallback(() => setFlash(null), [])
  const fail = useCallback((error: unknown, title?: string) => setFlash({ tone: 'danger', title, message: describeError(error) }), [])
  return { flash, show, clear, fail }
}

export function FlashRegion({ flash, onDismiss, className = '' }: { flash: Flash | null; onDismiss: () => void; className?: string }) {
  return (
    <div aria-live={flash?.tone === 'danger' ? 'assertive' : 'polite'} aria-atomic="true" className={className}>
      {flash ? (
        <div className="enter-fade relative">
          <Notice tone={flash.tone} title={flash.title} className="pr-14">
            {flash.message}
          </Notice>
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss message"
            className="absolute right-1.5 top-1.5 flex size-11 items-center justify-center rounded-full text-plum-soft hover:bg-plum/5 hover:text-plum"
          >
            <X aria-hidden className="size-4" />
          </button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * State for one modal at a time. Each opening gets a fresh key (fresh form state), and
 * focus returns to the control that opened it — or to the page when that control is gone.
 */
export function useDialog<T>() {
  const [state, setState] = useState<{ value: T; seq: number } | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const seq = useRef(0)
  const open = useCallback((value: T, from?: Element | null) => {
    const active = from ?? document.activeElement
    opener.current = active instanceof HTMLElement ? active : null
    seq.current += 1
    setState({ value, seq: seq.current })
  }, [])
  const close = useCallback(() => {
    setState(null)
    const target = opener.current
    opener.current = null
    window.requestAnimationFrame(() => {
      if (target?.isConnected && !target.hasAttribute('disabled')) target.focus()
      else document.getElementById('admin-main')?.focus()
    })
  }, [])
  return { value: state?.value ?? null, key: state?.seq ?? 0, open, close }
}

/** Focus an element after the current frame (for example, once a modal has opened). */
export function focusSoon(element: HTMLElement | null) {
  if (!element) return
  const id = window.requestAnimationFrame(() => element.focus())
  return () => window.cancelAnimationFrame(id)
}

// ---- lists --------------------------------------------------------------------

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  label,
  busy = false,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  label: string
  busy?: boolean
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1
  const to = Math.min(total, page * pageSize)
  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-between gap-3 pt-4">
      <p className="tabular text-sm text-plum-soft">
        {total === 0 ? 'Nothing to show' : `Showing ${from.toLocaleString('en-IN')}–${to.toLocaleString('en-IN')} of ${total.toLocaleString('en-IN')}`}
      </p>
      {pages > 1 ? (
        <div className="flex items-center gap-2">
          <MiniButton disabled={page <= 1 || busy} onClick={() => onPage(page - 1)}>
            <ChevronLeft aria-hidden className="size-4" />
            Previous
          </MiniButton>
          <span className="tabular px-1 text-sm text-plum-soft">
            Page {page} of {pages}
          </span>
          <MiniButton disabled={page >= pages || busy} onClick={() => onPage(page + 1)}>
            Next
            <ChevronRight aria-hidden className="size-4" />
          </MiniButton>
        </div>
      ) : null}
    </nav>
  )
}

/** Toggle-button group for filters. Each option reports its pressed state. */
export function FilterChips<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string; count?: number | null }[]
  onChange: (value: T) => void
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-2">
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-3.5 text-sm font-semibold transition-colors ${
              active ? 'border-plum bg-plum text-paper' : 'border-sand-strong bg-paper text-plum hover:border-plum-soft'
            }`}
          >
            {option.label}
            {option.count !== undefined && option.count !== null ? (
              <span
                className={`tabular rounded-full px-1.5 text-xs ${active ? 'bg-paper/20 text-paper' : 'bg-plum/[0.07] text-plum-soft'}`}
              >
                {option.count.toLocaleString('en-IN')}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
