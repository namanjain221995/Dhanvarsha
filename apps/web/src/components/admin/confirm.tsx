'use client'

import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Notice } from '@/components/ui/misc'

const noop = () => {}

/**
 * Confirmation for consequential actions. The description says what will happen in
 * plain words; the confirm button repeats the action's name.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  tone = 'primary',
  busy = false,
  error,
  confirmDisabled = false,
  onConfirm,
  onClose,
  wide = false,
}: {
  open: boolean
  title: string
  description?: React.ReactNode
  children?: React.ReactNode
  confirmLabel: string
  tone?: 'primary' | 'danger'
  busy?: boolean
  error?: React.ReactNode
  confirmDisabled?: boolean
  onConfirm: () => void
  onClose: () => void
  wide?: boolean
}) {
  return (
    <Dialog
      open={open}
      onClose={busy ? noop : onClose}
      title={title}
      description={description}
      wide={wide}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone === 'danger' ? 'danger' : 'primary'} busy={busy} disabled={confirmDisabled} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children || error ? (
        <>
          {children}
          {error ? (
            <Notice tone="danger" role="alert" className={children ? 'mt-4' : ''}>
              {error}
            </Notice>
          ) : null}
        </>
      ) : null}
    </Dialog>
  )
}
