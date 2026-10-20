'use client'

import { ChevronDown, ImageUp, Trash2 } from 'lucide-react'
import { forwardRef, useId, useRef, useState } from 'react'
import { Avatar } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'
import { uploadAsset } from '@/lib/admin/query'
import type { AssetKind, UploadedAsset } from '@/lib/admin/types'
import { isValidTimeZone, zonedInputToIso, zoneLabel } from '@/lib/admin/zoned'
import { localEquivalent } from '@/lib/time'
import { MiniButton } from './primitives'

/** Shared look of text-like controls. Add a width class (`controlClass` already has `w-full`). */
export const controlBase =
  'block rounded-[0.875rem] border bg-paper px-4 text-base text-plum placeholder:text-plum-faint outline-none ' +
  'transition-[border-color,box-shadow] duration-150 focus:border-primary focus:shadow-[0_0_0_3px_var(--primary-ring)] ' +
  'disabled:cursor-not-allowed disabled:bg-ivory disabled:text-plum-soft'

export const controlClass = `${controlBase} w-full`

const borderFor = (error?: string | null) => (error ? 'border-danger' : 'border-plum-faint/70')

type FrameProps = {
  id: string
  label: React.ReactNode
  hint?: React.ReactNode
  error?: string | null
  optional?: boolean
  className?: string
  children: React.ReactNode
  aside?: React.ReactNode
}

function FieldFrame({ id, label, hint, error, optional, className = '', children, aside }: FrameProps) {
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="block text-sm font-semibold text-plum">
          {label}
          {optional ? <span className="font-normal text-plum-soft"> (optional)</span> : null}
        </label>
        {aside}
      </div>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="mt-1.5 text-sm text-plum-soft">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}

const describedBy = (id: string, hint: unknown, error: unknown) =>
  [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(' ') || undefined

type InputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'id'> & {
  label: React.ReactNode
  hint?: React.ReactNode
  error?: string | null
  optional?: boolean
  id?: string
}

/** Text input with the console's label, hint, error and optional marker. */
export const InputField = forwardRef<HTMLInputElement, InputProps>(function InputField(
  { label, hint, error, optional, id, className = '', ...rest },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  return (
    <FieldFrame id={inputId} label={label} hint={hint} error={error} optional={optional} className={className}>
      <input
        ref={ref}
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, hint, error)}
        className={`${controlClass} min-h-12 ${borderFor(error)}`}
        {...rest}
      />
    </FieldFrame>
  )
})

type TextAreaProps = Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> & {
  label: React.ReactNode
  hint?: React.ReactNode
  error?: string | null
  optional?: boolean
  id?: string
}

export function TextAreaField({ label, hint, error, optional, id, className = '', rows = 4, maxLength, value, ...rest }: TextAreaProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  const length = typeof value === 'string' ? value.length : 0
  const nearLimit = maxLength !== undefined && length > maxLength * 0.8
  return (
    <FieldFrame
      id={inputId}
      label={label}
      hint={hint}
      error={error}
      optional={optional}
      className={className}
      aside={
        nearLimit ? (
          <span className="tabular text-xs text-plum-soft">
            {length.toLocaleString('en-IN')} of {maxLength?.toLocaleString('en-IN')}
          </span>
        ) : null
      }
    >
      <textarea
        id={inputId}
        rows={rows}
        maxLength={maxLength}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, hint, error)}
        className={`${controlClass} py-3 leading-relaxed ${borderFor(error)}`}
        {...rest}
      />
    </FieldFrame>
  )
}

type SelectProps = Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'id'> & {
  label: React.ReactNode
  hint?: React.ReactNode
  error?: string | null
  id?: string
}

export function SelectField({ label, hint, error, id, className = '', children, ...rest }: SelectProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  return (
    <FieldFrame id={inputId} label={label} hint={hint} error={error} className={className}>
      <div className="relative">
        <select
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(inputId, hint, error)}
          className={`${controlClass} min-h-12 appearance-none pr-11 ${borderFor(error)}`}
          {...rest}
        >
          {children}
        </select>
        <ChevronDown aria-hidden className="pointer-events-none absolute right-4 top-1/2 size-4 -translate-y-1/2 text-plum-soft" />
      </div>
    </FieldFrame>
  )
}

export function CheckboxField({
  label,
  description,
  checked,
  onChange,
  disabled,
  className = '',
}: {
  label: React.ReactNode
  description?: React.ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  className?: string
}) {
  const id = useId()
  return (
    <div className={`flex items-start gap-3 ${className}`}>
      <span className="flex size-11 shrink-0 items-center justify-center">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby={description ? `${id}-desc` : undefined}
          className="size-5 cursor-pointer accent-plum disabled:cursor-not-allowed"
        />
      </span>
      <span className="min-w-0 py-2.5">
        <label htmlFor={id} className="block cursor-pointer font-semibold leading-snug text-plum">
          {label}
        </label>
        {description ? (
          <span id={`${id}-desc`} className="mt-0.5 block text-sm text-plum-soft">
            {description}
          </span>
        ) : null}
      </span>
    </div>
  )
}

/** On/off setting. The knob position and the words "On"/"Off" carry the state, not colour. */
export function SwitchField({
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  label: React.ReactNode
  description?: React.ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0">
        <p id={`${id}-label`} className="font-semibold leading-snug text-plum">
          {label}
        </p>
        {description ? (
          <p id={`${id}-desc`} className="mt-0.5 text-sm text-plum-soft">
            {description}
          </p>
        ) : null}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-desc` : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="group inline-flex min-h-11 shrink-0 items-center gap-2 rounded-full pl-1 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span aria-hidden className="tabular w-7 text-right text-sm font-semibold text-plum-soft">
          {checked ? 'On' : 'Off'}
        </span>
        <span
          aria-hidden
          className={`relative h-7 w-12 rounded-full transition-colors duration-150 ${checked ? 'bg-plum' : 'bg-plum/20'}`}
        >
          <span
            className={`absolute left-0 top-0.5 size-6 rounded-full bg-paper shadow-[0_1px_3px_rgb(43_22_49/0.3)] transition-transform duration-150 ${
              checked ? 'translate-x-[1.375rem]' : 'translate-x-0.5'
            }`}
          />
        </span>
      </button>
    </div>
  )
}

export function RadioCards<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
  disabled,
  columns = 2,
  hint,
}: {
  legend: React.ReactNode
  name: string
  value: T
  options: { value: T; label: React.ReactNode; description?: React.ReactNode; disabled?: boolean }[]
  onChange: (value: T) => void
  disabled?: boolean
  columns?: 1 | 2 | 3
  hint?: React.ReactNode
}) {
  const grid = { 1: '', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3' }[columns]
  return (
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="mb-1.5 text-sm font-semibold text-plum">{legend}</legend>
      {hint ? <p className="-mt-0.5 mb-2 text-sm text-plum-soft">{hint}</p> : null}
      <div className={`grid gap-2 ${grid}`}>
        {options.map((option) => {
          const checked = option.value === value
          return (
            <label
              key={option.value}
              className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-2xl border bg-paper px-4 py-3 transition-colors has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 has-[:focus-visible]:outline has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-primary ${
                checked ? 'border-plum ring-1 ring-plum' : 'border-sand-strong hover:border-plum-soft'
              }`}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={checked}
                disabled={option.disabled}
                onChange={() => onChange(option.value)}
                className="mt-0.5 size-4 shrink-0 accent-plum focus-visible:outline-none"
              />
              <span className="min-w-0">
                <span className="block font-semibold leading-snug text-plum">{option.label}</span>
                {option.description ? <span className="mt-0.5 block text-sm text-plum-soft">{option.description}</span> : null}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

/** `datetime-local` read and written in the programme timezone, with the zone spelled out. */
export function ZonedDateTimeField({
  label,
  value,
  onChange,
  timeZone,
  hint,
  error,
  disabled,
  optional,
  className = '',
}: {
  label: React.ReactNode
  value: string
  onChange: (value: string) => void
  timeZone: string
  hint?: React.ReactNode
  error?: string | null
  disabled?: boolean
  optional?: boolean
  className?: string
}) {
  const id = useId()
  const validZone = isValidTimeZone(timeZone)
  const iso = value && validZone ? zonedInputToIso(value, timeZone) : null
  const local = iso ? localEquivalent(iso, timeZone) : null
  const zoneText = validZone ? `Time in ${zoneLabel(timeZone, iso)}.` : 'Set a valid programme timezone first.'
  return (
    <FieldFrame
      id={id}
      label={label}
      optional={optional}
      error={error}
      className={className}
      hint={
        <>
          {hint ? <>{hint} </> : null}
          {zoneText}
          {local ? ` That is ${local}.` : ''}
        </>
      }
    >
      <input
        id={id}
        type="datetime-local"
        step={60}
        value={value}
        disabled={disabled || !validZone}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, true, error)}
        className={`${controlClass} tabular min-h-12 ${borderFor(error)}`}
      />
    </FieldFrame>
  )
}

/** Image upload with preview, replace and remove. Uploads immediately; the form saves the id. */
export function ImageField({
  label,
  kind,
  url,
  name,
  onUploaded,
  onRemove,
  disabled,
  shape = 'square',
  hint,
}: {
  label: string
  kind: AssetKind
  url: string | null
  name: string
  onUploaded: (asset: UploadedAsset) => void
  onRemove: () => void
  disabled?: boolean
  shape?: 'square' | 'round' | 'banner'
  hint?: React.ReactNode
}) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onFile = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      onUploaded(await uploadAsset(kind, file))
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  const preview =
    shape === 'banner' ? (
      <div className="relative aspect-[16/5] w-full max-w-md overflow-hidden rounded-2xl border border-sand bg-ivory">
        {url ? (
          // eslint-disable-next-line @next/next/no-img-element -- API serves pre-optimised WebP
          <img src={url} alt="" className="absolute inset-0 size-full object-cover" />
        ) : (
          <span className="placeholder-bar absolute inset-3 flex items-center justify-center text-sm text-plum-soft">
            No banner
          </span>
        )}
      </div>
    ) : (
      <Avatar src={url} name={name} size={72} rounded={shape === 'round' ? 'full' : 'xl'} />
    )

  return (
    <div role="group" aria-labelledby={`${id}-label`}>
      <p id={`${id}-label`} className="mb-2 text-sm font-semibold text-plum">
        {label}
      </p>
      <div className={`flex gap-4 ${shape === 'banner' ? 'flex-col items-start' : 'items-center'}`}>
        {preview}
        <div className="flex flex-wrap gap-2">
          <MiniButton busy={busy} disabled={disabled} onClick={() => input.current?.click()} aria-describedby={`${id}-hint`}>
            <ImageUp aria-hidden className="size-4" />
            {url ? 'Replace' : 'Upload'}
            <span className="sr-only"> {label.toLowerCase()}</span>
          </MiniButton>
          {url ? (
            <MiniButton variant="quiet" disabled={disabled || busy} onClick={onRemove}>
              <Trash2 aria-hidden className="size-4" />
              Remove
              <span className="sr-only"> {label.toLowerCase()}</span>
            </MiniButton>
          ) : null}
        </div>
      </div>
      <p id={`${id}-hint`} className="mt-2 text-sm text-plum-soft">
        {hint ?? 'JPEG, PNG, WebP or GIF, up to 5 MB.'}
      </p>
      {error ? (
        <p role="alert" className="mt-1.5 text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(event) => void onFile(event.target.files?.[0])}
      />
    </div>
  )
}
