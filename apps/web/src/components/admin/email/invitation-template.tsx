'use client'

import { Eye, Save, Send, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError, fieldErrors, isApiError } from '@/lib/admin/errors'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import type { InvitationPreview, InvitationTemplate, InvitationWording, SendToResult } from '@/lib/admin/types'
import { formatWhen } from '@/lib/admin/zoned'
import { ConfirmDialog } from '../confirm'
import { InputField, TextAreaField } from '../form'
import { LoadError, MiniButton, SkeletonBlock, type useFlash } from '../primitives'

const PATH = '/admin/email/templates/invitation'
const FIELDS: (keyof InvitationWording)[] = ['subject', 'heading', 'intro', 'closing', 'button_label']

const same = (a: InvitationWording, b: InvitationWording) => FIELDS.every((key) => a[key].trim() === b[key].trim())

type Flash = ReturnType<typeof useFlash>

/**
 * The invitation's wording: edit it, see the result as participants will, and send real invitations.
 * Only the wording is editable; the user ID, password, button and footer are always added.
 */
export function InvitationTemplatePanel({ timeZone, flash, onInvited }: { timeZone: string; flash: Flash; onInvited: (result: SendToResult) => void }) {
  const query = useAdminQuery<InvitationTemplate>(PATH)
  const [base, setBase] = useState<InvitationTemplate | null>(null)
  const [draft, setDraft] = useState<InvitationWording | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const server = query.data
  const dirty = draft !== null && base !== null && !same(draft, base.wording)
  // Adopt fresh server data whenever there are no unsaved edits.
  if (server && (base === null || (server !== base && !dirty))) {
    setBase(server)
    setDraft(server.wording)
  }

  if (!draft || !base) {
    return (
      <section aria-labelledby="invitation-title" className="mt-5 rounded-[var(--radius-card)] border border-sand bg-paper px-4 py-4 sm:px-6 sm:py-5">
        <h2 id="invitation-title" className="text-[1.05rem] font-bold text-plum">
          Invitation email
        </h2>
        <div className="mt-4">
          {query.error ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the invitation wording" /> : <SkeletonBlock className="h-64 w-full" />}
        </div>
      </section>
    )
  }

  const update = (key: keyof InvitationWording, value: string) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
    setFormError(null)
    if (errors[key]) {
      setErrors((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  const save = async (): Promise<boolean> => {
    setSaving(true)
    setErrors({})
    setFormError(null)
    const backToDefaults = same(draft, base.defaults)
    try {
      const updated = backToDefaults
        ? await adminAction<InvitationTemplate>(PATH, { method: 'DELETE' })
        : await adminAction<InvitationTemplate>(PATH, { method: 'PUT', body: draft })
      query.mutate(() => updated)
      setBase(updated)
      setDraft(updated.wording)
      flash.show({
        tone: 'success',
        message: backToDefaults ? 'Invitations use the built-in wording again.' : 'Invitation wording saved. Every invitation sent from now on uses it.',
      })
      return true
    } catch (error) {
      const fields = fieldErrors(error)
      setErrors(fields)
      setFormError(Object.keys(fields).length ? 'Some fields need attention.' : describeError(error))
      return false
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="invitation-title" className="mt-5 rounded-[var(--radius-card)] border border-sand bg-paper">
      <div className="px-4 pt-4 sm:px-6 sm:pt-5">
        <h2 id="invitation-title" className="text-[1.05rem] font-bold text-plum">
          Invitation email
        </h2>
        <p className="mt-0.5 max-w-3xl text-sm text-plum-soft">
          Change what the invitation says. Every invitation always includes the participant’s user ID (their email address), their
          own password, the sign-in button, when sign-in opens and the help contact, so these cannot be removed by mistake.
          {base.customised && base.updated_at ? ` Last saved ${formatWhen(base.updated_at, timeZone)}.` : ' It uses the built-in wording now.'}
        </p>
      </div>
      <div className="grid gap-6 px-4 pb-5 pt-4 sm:px-6 sm:pb-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <form
          noValidate
          className="min-w-0 space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (dirty) void save()
          }}
        >
          <InputField label="Subject" value={draft.subject} maxLength={200} error={errors.subject} onChange={(e) => update('subject', e.target.value)} />
          <InputField label="Heading" value={draft.heading} maxLength={120} error={errors.heading} onChange={(e) => update('heading', e.target.value)} />
          <TextAreaField
            label="Message"
            rows={6}
            maxLength={3000}
            value={draft.intro}
            error={errors.intro}
            hint="Shown above the user ID and password. Leave an empty line between paragraphs."
            onChange={(e) => update('intro', e.target.value)}
          />
          <TextAreaField
            label="Closing note"
            optional
            rows={5}
            maxLength={3000}
            value={draft.closing}
            error={errors.closing}
            hint="Shown below the sign-in button, for example what to bring or who to ask."
            onChange={(e) => update('closing', e.target.value)}
          />
          <InputField
            label="Button text"
            value={draft.button_label}
            maxLength={60}
            error={errors.button_label}
            onChange={(e) => update('button_label', e.target.value)}
          />
          <div className="rounded-2xl bg-ivory px-4 py-3 text-sm">
            <p className="font-semibold text-plum">Placeholders</p>
            <p className="mt-0.5 text-plum-soft">Type these anywhere above; each email fills them in.</p>
            <dl className="mt-2 grid gap-x-3 gap-y-1 sm:grid-cols-[auto_minmax(0,1fr)]">
              {base.placeholders.map((item) => (
                <div key={item.token} className="contents">
                  <dt>
                    <code className="rounded-md bg-paper px-1.5 py-0.5 font-semibold text-plum">{item.token}</code>
                  </dt>
                  <dd className="text-plum-soft">{item.meaning}</dd>
                </div>
              ))}
            </dl>
          </div>
          {formError ? (
            <Notice tone="danger" role="alert">
              {formError}
            </Notice>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" busy={saving} disabled={!dirty}>
              {saving ? null : <Save aria-hidden className="size-4" />}
              Save wording
            </Button>
            {dirty ? (
              <MiniButton
                variant="quiet"
                onClick={() => {
                  setDraft(base.wording)
                  setErrors({})
                  setFormError(null)
                }}
              >
                <Undo2 aria-hidden className="size-4" />
                Discard changes
              </MiniButton>
            ) : null}
            {!same(draft, base.defaults) ? (
              <MiniButton
                variant="quiet"
                onClick={() => {
                  setDraft(base.defaults)
                  setErrors({})
                  setFormError(null)
                }}
              >
                Use the built-in wording
              </MiniButton>
            ) : null}
          </div>
          {dirty ? <p className="text-sm text-gold-deep">You have unsaved changes. Invitations keep the saved wording until you save.</p> : null}
        </form>

        <div className="min-w-0 space-y-4">
          <LivePreview wording={draft} />
          <SendInvitations dirty={dirty} save={save} saving={saving} onSent={onInvited} />
        </div>
      </div>
    </section>
  )
}

function LivePreview({ wording }: { wording: InvitationWording }) {
  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [height, setHeight] = useState(720)
  const frame = useRef<HTMLIFrameElement>(null)
  const key = JSON.stringify(wording)

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      adminAction<InvitationPreview>(`${PATH}/preview`, { body: JSON.parse(key) as InvitationWording, signal: controller.signal })
        .then((result) => {
          setPreview(result)
          setProblem(null)
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return
          if (isApiError(error) && error.status === 422) setProblem('The preview updates once the fields above are valid.')
          else setProblem(describeError(error))
        })
    }, 350)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [key])

  return (
    <div className="overflow-hidden rounded-2xl border border-sand bg-ivory">
      <div className="flex items-start gap-2 border-b border-sand px-4 py-3">
        <Eye aria-hidden className="mt-0.5 size-4 shrink-0 text-plum-soft" />
        <div className="min-w-0 text-sm">
          <p className="font-semibold text-plum">How participants see it</p>
          <p className="break-words text-plum-soft">
            <span className="font-semibold text-plum">Subject:</span> {preview?.subject ?? '…'}
          </p>
        </div>
      </div>
      {problem ? <p className="border-b border-sand px-4 py-2 text-sm text-gold-deep">{problem}</p> : null}
      {preview ? (
        <iframe
          ref={frame}
          title="Invitation email preview"
          // No scripts ever run inside; same-origin only so the frame can be sized to its content.
          sandbox="allow-same-origin"
          srcDoc={preview.html}
          // A picture of the email: its links would otherwise open the site inside the frame.
          className="pointer-events-none block w-full bg-[#f7f2ea]"
          style={{ height }}
          onLoad={() => {
            const doc = frame.current?.contentDocument
            if (doc) setHeight(Math.min(Math.max(doc.documentElement.scrollHeight, 360), 2000))
          }}
        />
      ) : (
        <SkeletonBlock className="m-4 h-96" />
      )}
      <p className="border-t border-sand px-4 py-2.5 text-sm text-plum-soft">
        Example participant: Asha Rao. Each real invitation shows that person’s own email address and password.
      </p>
    </div>
  )
}

function SendInvitations({
  dirty,
  save,
  saving,
  onSent,
}: {
  dirty: boolean
  save: () => Promise<boolean>
  saving: boolean
  onSent: (result: SendToResult) => void
}) {
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const count = to.split(/[\s,;]+/).filter((part) => part.includes('@')).length

  const ask = (event: React.FormEvent) => {
    event.preventDefault()
    if (!count) {
      setError('Enter at least one full email address, such as you@example.com.')
      return
    }
    setError(null)
    setConfirming(true)
  }

  const send = async () => {
    setBusy(true)
    try {
      if (dirty && !(await save())) {
        setConfirming(false)
        return
      }
      const result = await adminAction<SendToResult>('/admin/invitations/send-to', { body: { to }, timeoutMs: 60_000 })
      setConfirming(false)
      onSent(result)
      setTo('')
    } catch (failure) {
      setConfirming(false)
      setError(describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={ask} noValidate className="space-y-3 rounded-2xl border border-sand px-4 py-4">
      <InputField
        label="Send invitations to"
        inputMode="email"
        autoComplete="off"
        spellCheck={false}
        autoCapitalize="none"
        value={to}
        onChange={(e) => setTo(e.target.value)}
        error={error}
        placeholder="asha@example.com, ravi@example.com"
        hint="Separate addresses with commas (up to 50). Each person gets a real invitation with their own password and can sign in straight away. People not on the participant list yet are added automatically."
      />
      <Button type="submit" busy={busy || (saving && dirty)}>
        {busy ? null : <Send aria-hidden className="size-4" />}
        {dirty ? 'Save wording and send invitations' : 'Send invitations'}
      </Button>
      {confirming ? (
        <ConfirmDialog
          open
          title={`Send ${count === 1 ? 'an invitation' : `${count} invitations`}?`}
          description={
            <>
              Each person gets an email with their user ID (their email address) and their own password.
              {dirty ? ' Your unsaved wording is saved first.' : ''} Anyone who was invited before gets a new password, and their old one
              stops working.
            </>
          }
          confirmLabel="Send invitations"
          busy={busy}
          onConfirm={() => void send()}
          onClose={() => setConfirming(false)}
        />
      ) : null}
    </form>
  )
}
