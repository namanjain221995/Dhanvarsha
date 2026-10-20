import { forwardRef } from 'react'
import { LoaderCircle } from 'lucide-react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet'
type Size = 'md' | 'lg' | 'sm'

const variants: Record<Variant, string> = {
  primary:
    'bg-primary text-white hover:bg-primary-deep active:bg-primary-deep disabled:bg-plum/10 disabled:text-plum-soft',
  secondary:
    'bg-paper text-plum border border-plum-faint/60 hover:border-plum-soft hover:bg-white disabled:text-plum-faint disabled:border-sand',
  ghost: 'bg-transparent text-primary hover:bg-primary-wash disabled:text-plum-faint',
  danger: 'bg-danger text-white hover:bg-[#8f1e31] disabled:bg-plum/10 disabled:text-plum-soft',
  quiet: 'bg-transparent text-plum-soft hover:text-plum hover:bg-plum/5 disabled:text-plum-faint',
}

const sizes: Record<Size, string> = {
  sm: 'min-h-10 px-3.5 text-sm gap-1.5',
  md: 'min-h-11 px-5 text-[0.95rem] gap-2',
  lg: 'min-h-13 px-6 text-base gap-2.5',
}

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  size?: Size
  busy?: boolean
  block?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', busy = false, block = false, className = '', children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      data-variant={variant}
      className={`inline-flex select-none items-center justify-center rounded-full font-semibold transition-colors duration-150 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${block ? 'w-full' : ''} ${className}`}
      {...rest}
    >
      {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {children}
    </button>
  )
})
