import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'

export function initials(name: string | null | undefined): string {
  const parts = (name ?? '').replace(/['’]/g, '').split(/\s+/).filter((p) => /[\p{L}\p{N}]/u.test(p[0] ?? ''))
  return (parts.slice(0, 2).map((p) => p[0]).join('') || '?').toUpperCase()
}

const swatches = ['#efe8ff', '#fbe8f5', '#e1f5f2', '#fbf1dc', '#e6e8ff', '#fde6ee']

/** Image with a graceful initials fallback (no broken-image icons). */
export function Avatar({
  src,
  name,
  size = 48,
  rounded = 'full',
  className = '',
}: {
  src?: string | null
  name: string | null | undefined
  size?: number
  rounded?: 'full' | 'xl'
  className?: string
}) {
  const radius = rounded === 'full' ? 'rounded-full' : 'rounded-[14px]'
  const tint = swatches[(name ?? '').length % swatches.length]
  return (
    <span
      aria-hidden
      className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden ${radius} ${className}`}
      style={{ width: size, height: size, background: tint }}
    >
      <span className="type-condensed font-bold text-plum" style={{ fontSize: Math.round(size * 0.36) }}>
        {initials(name)}
      </span>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- images are pre-optimised WebP from the API
        <img
          src={src}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
          onError={(event) => {
            event.currentTarget.style.display = 'none'
          }}
        />
      ) : null}
    </span>
  )
}

type Tone = 'info' | 'success' | 'warning' | 'danger'
const toneStyles: Record<Tone, string> = {
  info: 'bg-primary-wash text-plum border-primary/20',
  success: 'bg-success-wash text-plum border-success/25',
  warning: 'bg-gold-wash text-plum border-gold/40',
  danger: 'bg-danger-wash text-plum border-danger/25',
}
const toneIcon: Record<Tone, React.ReactNode> = {
  info: <Info aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />,
  success: <CheckCircle2 aria-hidden className="mt-0.5 size-5 shrink-0 text-success" />,
  warning: <AlertTriangle aria-hidden className="mt-0.5 size-5 shrink-0 text-gold-deep" />,
  danger: <XCircle aria-hidden className="mt-0.5 size-5 shrink-0 text-danger" />,
}

export function Notice({
  tone = 'info',
  title,
  children,
  action,
  className = '',
  role,
}: {
  tone?: Tone
  title?: React.ReactNode
  children?: React.ReactNode
  action?: React.ReactNode
  className?: string
  role?: 'alert' | 'status'
}) {
  return (
    <div role={role} className={`flex gap-3 rounded-2xl border px-4 py-3.5 text-[0.95rem] ${toneStyles[tone]} ${className}`}>
      {toneIcon[tone]}
      <div className="min-w-0 flex-1">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={title ? 'mt-0.5 text-plum-soft' : ''}>{children}</div> : null}
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    </div>
  )
}

export function VisuallyHidden({ children }: { children: React.ReactNode }) {
  return <span className="sr-only">{children}</span>
}
