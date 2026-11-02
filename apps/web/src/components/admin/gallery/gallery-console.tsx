'use client'

import { Copy, Film, RotateCcw, Save, ScanFace, Search, Send, Trash2, Undo2, UserPen } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Coin } from '@/components/gallery/coin'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError, fieldErrors, isApiError } from '@/lib/admin/errors'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import type {
  AdminGalleryMedia,
  AdminGalleryMediaList,
  AdminGalleryPeople,
  AdminGalleryPerson,
  GallerySummary,
  InvitationTemplate,
  InvitationWording,
} from '@/lib/admin/types'
import { plural } from '@/lib/admin/labels'
import { countLabel } from '@/lib/gallery'
import { ConfirmDialog } from '../confirm'
import { AnalysisDialog } from './analysis-dialog'
import { LivePreview } from '../email/invitation-template'
import { InputField, SelectField, TextAreaField } from '../form'
import { EmptyState, FilterChips, FlashRegion, KeyValue, LoadError, MiniButton, PageHeader, Pagination, Panel, SkeletonBlock, StatusChip, useFlash } from '../primitives'

type Flash = ReturnType<typeof useFlash>

const TEMPLATE = '/admin/email/templates/gallery'
const MEDIA_PAGE = 40
const PEOPLE_PAGE = 40

/**
 * The organisers' side of the photo gallery: the link to share, the email that announces it, and
 * removing what should not be there. Anyone with the link can add files, so every file names who
 * sent it. Sorting faces happens by itself.
 */
export function GalleryConsolePage() {
  const flash = useFlash()
  const summary = useAdminQuery<GallerySummary>('/admin/gallery')
  const { data, error, reload } = summary

  return (
    <div>
      <PageHeader
        title="Photo gallery"
        description="After the event, anyone with the link adds a selfie and their name, shares photos and videos, and finds every one they appear in."
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-4" />
      {error && !data ? <LoadError error={error} onRetry={() => void reload()} what="the photo gallery" /> : null}
      {!data && !error ? <SkeletonBlock className="mt-5 h-40 w-full" /> : null}
      {data ? (
        <div className="mt-5 space-y-5">
          <SharePanel summary={data} flash={flash} />
          <EmailPanel summary={data} flash={flash} onSent={() => void reload()} />
          <FilesPanel flash={flash} onChanged={() => void reload()} />
          <PeoplePanel flash={flash} onChanged={() => void reload()} />
        </div>
      ) : null}
    </div>
  )
}

function SharePanel({ summary, flash }: { summary: GallerySummary; flash: Flash }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary.share_url)
      flash.show({ tone: 'success', message: 'Link copied.' })
    } catch {
      flash.show({ tone: 'warning', message: `Copy this link by hand: ${summary.share_url}` })
    }
  }
  const size = summary.bytes >= 1024 ** 3 ? `${(summary.bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(summary.bytes / 1024 ** 2)} MB`
  return (
    <Panel title="Link to share" description="It needs no sign-in: anyone who has it can see the people and photos, and add their own.">
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 break-all rounded-xl bg-ivory px-3 py-2.5 text-sm font-semibold text-plum">{summary.share_url}</code>
        <MiniButton onClick={() => void copy()}>
          <Copy aria-hidden className="size-4" />
          Copy link
        </MiniButton>
      </div>
      <KeyValue
        className="mt-4"
        items={[
          ['People with a selfie', summary.enrolled.toLocaleString('en-IN')],
          ['Unnamed groups of faces', summary.unnamed.toLocaleString('en-IN')],
          ['Photos', summary.photos.toLocaleString('en-IN')],
          ['Videos', summary.videos.toLocaleString('en-IN')],
          ['Waiting to be sorted', summary.pending.toLocaleString('en-IN')],
          ['Could not be read', summary.failed.toLocaleString('en-IN')],
          ['Space used', size],
          [
            'Stored in',
            summary.storage.where === 's3'
              ? `S3 bucket ${summary.storage.bucket}, with a folder per named person: about $${(summary.storage.monthly_usd ?? 0).toFixed(2)} a month`
              : 'This machine',
          ],
          ['Close matches', summary.model_enabled ? 'Checked by the vision model' : 'Left unmatched: no vision model is set'],
        ]}
      />
    </Panel>
  )
}

const FIELDS: (keyof InvitationWording)[] = ['subject', 'heading', 'intro', 'closing', 'button_label']
const same = (a: InvitationWording, b: InvitationWording) => FIELDS.every((key) => a[key].trim() === b[key].trim())

function EmailPanel({ summary, flash, onSent }: { summary: GallerySummary; flash: Flash; onSent: () => void }) {
  const query = useAdminQuery<InvitationTemplate>(TEMPLATE)
  const [base, setBase] = useState<InvitationTemplate | null>(null)
  const [draft, setDraft] = useState<InvitationWording | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sending, setSending] = useState(false)
  const [testTo, setTestTo] = useState('')
  const [testing, setTesting] = useState(false)

  const server = query.data
  const dirty = draft !== null && base !== null && !same(draft, base.wording)
  // Adopt fresh server data whenever there are no unsaved edits.
  if (server && (base === null || (server !== base && !dirty))) {
    setBase(server)
    setDraft(server.wording)
  }

  const { to_send: toSend, already, reserved } = summary.email

  const update = (key: keyof InvitationWording, value: string) => {
    setDraft((previous) => (previous ? { ...previous, [key]: value } : previous))
    setErrors((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  const save = async (): Promise<boolean> => {
    if (!draft || !base) return false
    setSaving(true)
    try {
      const updated = same(draft, base.defaults)
        ? await adminAction<InvitationTemplate>(TEMPLATE, { method: 'DELETE' })
        : await adminAction<InvitationTemplate>(TEMPLATE, { method: 'PUT', body: draft })
      query.mutate(() => updated)
      setBase(updated)
      setDraft(updated.wording)
      flash.show({ tone: 'success', message: 'Gallery email wording saved.' })
      return true
    } catch (failure) {
      const fields = fieldErrors(failure)
      setErrors(fields)
      if (!Object.keys(fields).length) flash.fail(failure)
      return false
    } finally {
      setSaving(false)
    }
  }

  const send = async () => {
    setSending(true)
    try {
      if (dirty && !(await save())) return
      const result = await adminAction<{ queued: number; already: number; reserved: number }>('/admin/gallery/email/send', { timeoutMs: 60_000 })
      flash.show({ tone: 'success', message: `The gallery email is on its way to ${plural(result.queued, 'person', 'people')}. See its progress on the Email page.` })
      onSent()
    } catch (failure) {
      flash.fail(failure)
    } finally {
      setSending(false)
      setConfirming(false)
    }
  }

  const sendTest = async (event: React.FormEvent) => {
    event.preventDefault()
    setTesting(true)
    try {
      await adminAction('/admin/email/test', { body: { to: testTo, template: 'gallery' } })
      flash.show({ tone: 'success', message: `Preview sent to ${testTo}.` })
    } catch (failure) {
      flash.fail(failure)
    } finally {
      setTesting(false)
    }
  }

  return (
    <Panel
      title="Gallery email"
      description="Tells everyone on the participant list the event's photos are here. Each person's button says who they are, so their name is filled in and only the selfie is left. It does not touch their sign-in link for voting."
    >
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => setConfirming(true)} disabled={toSend === 0} busy={sending}>
          {sending ? null : <Send aria-hidden className="size-4" />}
          {toSend === 0 ? 'Everyone has it' : `Send to ${plural(toSend, 'person', 'people')}`}
        </Button>
        <p className="text-sm text-plum-soft">
          {already > 0 ? `${plural(already, 'person has', 'people have')} it already. ` : ''}
          {reserved > 0 ? `${plural(reserved, 'test address', 'test addresses')} (such as example.com) will be skipped.` : ''}
        </p>
      </div>
      {confirming ? (
        <ConfirmDialog
          open
          title={`Send the gallery email to ${plural(toSend, 'person', 'people')}?`}
          description={<>This sends a real email to each of them, once.{dirty ? ' Your unsaved wording is saved first.' : ''}</>}
          confirmLabel="Send the email"
          busy={sending}
          onConfirm={() => void send()}
          onClose={() => setConfirming(false)}
        />
      ) : null}

      {query.error && !draft ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the email wording" /> : null}
      {draft && base ? (
        <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
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
              rows={5}
              maxLength={3000}
              value={draft.intro}
              error={errors.intro}
              hint="Shown above the button. Leave an empty line between paragraphs."
              onChange={(e) => update('intro', e.target.value)}
            />
            <TextAreaField
              label="Closing note"
              optional
              rows={4}
              maxLength={3000}
              value={draft.closing}
              error={errors.closing}
              hint="Shown below the button."
              onChange={(e) => update('closing', e.target.value)}
            />
            <InputField label="Button text" value={draft.button_label} maxLength={60} error={errors.button_label} onChange={(e) => update('button_label', e.target.value)} />
            <p className="text-sm text-plum-soft">
              You can use {base.placeholders.map((item) => item.token).join(', ')}: each email fills them in.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" busy={saving} disabled={!dirty}>
                {saving ? null : <Save aria-hidden className="size-4" />}
                Save wording
              </Button>
              {dirty ? (
                <MiniButton variant="quiet" onClick={() => setDraft(base.wording)}>
                  <Undo2 aria-hidden className="size-4" />
                  Discard changes
                </MiniButton>
              ) : null}
              {!same(draft, base.defaults) ? (
                <MiniButton variant="quiet" onClick={() => setDraft(base.defaults)}>
                  Use the built-in wording
                </MiniButton>
              ) : null}
            </div>
          </form>
          <div className="min-w-0 space-y-4">
            <LivePreview
              wording={draft}
              path={TEMPLATE}
              title="Gallery email preview"
              note="Example participant: Asha Rao. Each real email has that person’s own link."
            />
            <form onSubmit={(event) => void sendTest(event)} noValidate className="space-y-3 rounded-2xl border border-sand px-4 py-4">
              <InputField
                label="Send a preview to"
                inputMode="email"
                autoComplete="off"
                spellCheck={false}
                autoCapitalize="none"
                value={testTo}
                placeholder="you@example.com"
                onChange={(e) => setTestTo(e.target.value)}
              />
              <Button type="submit" variant="secondary" busy={testing} disabled={!testTo.includes('@')}>
                Send a preview
              </Button>
            </form>
          </div>
        </div>
      ) : null}
    </Panel>
  )
}

type FileFilter = 'all' | 'pending' | 'failed'

function FilesPanel({ flash, onChanged }: { flash: Flash; onChanged: () => void }) {
  const [filter, setFilter] = useState<FileFilter>('all')
  const [page, setPage] = useState(1)
  const query = useAdminQuery<AdminGalleryMediaList>('/admin/gallery/media', { status: filter, offset: (page - 1) * MEDIA_PAGE, limit: MEDIA_PAGE })
  const [removing, setRemoving] = useState<AdminGalleryMedia | null>(null)
  const [analysing, setAnalysing] = useState<AdminGalleryMedia | null>(null)
  const [busy, setBusy] = useState(false)

  const remove = async () => {
    if (!removing) return
    setBusy(true)
    try {
      await adminAction(`/admin/gallery/media/${removing.id}`, { method: 'DELETE' })
      flash.show({ tone: 'success', message: `${removing.name || 'The file'} was removed.` })
      void query.reload()
      onChanged()
    } catch (failure) {
      flash.fail(failure)
    } finally {
      setBusy(false)
      setRemoving(null)
    }
  }

  const retry = async (item: AdminGalleryMedia) => {
    try {
      await adminAction(`/admin/gallery/media/${item.id}/retry`)
      flash.show({ tone: 'success', message: `${item.name || 'The file'} will be tried again.` })
      void query.reload()
      onChanged()
    } catch (failure) {
      flash.fail(failure)
    }
  }

  return (
    <Panel title="Files" description="Every file anyone has shared, newest first. Remove anything that should not be here.">
      <FilterChips
        label="Show files"
        value={filter}
        onChange={(value) => {
          setFilter(value)
          setPage(1)
        }}
        options={[
          { value: 'all', label: 'All' },
          { value: 'pending', label: 'Waiting' },
          { value: 'failed', label: 'Could not be read' },
        ]}
      />
      {query.error && !query.data ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the files" /> : null}
      {query.data && query.data.media.length === 0 ? (
        <EmptyState title={filter === 'all' ? 'Nothing shared yet' : 'No files here'}>Files appear here as people share them.</EmptyState>
      ) : null}
      {query.data && query.data.media.length > 0 ? (
        <ul className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-3">
          {query.data.media.map((item) => (
            <li key={item.id} className="overflow-hidden rounded-2xl border border-sand bg-ivory">
              <a
                href={item.download_url ?? undefined}
                target="_blank"
                rel="noreferrer"
                className="relative block aspect-[4/3] bg-plum/10"
                aria-label={item.download_url ? `Open ${item.name}` : undefined}
              >
                {item.thumb_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- a small WebP from the API
                  <img src={item.thumb_url} alt="" loading="lazy" className="size-full object-cover" />
                ) : (
                  <span className="flex size-full items-center justify-center text-sm text-plum-soft">No picture yet</span>
                )}
                {item.type === 'video' ? (
                  <span className="absolute bottom-1.5 left-1.5 rounded-full bg-plum/80 p-1 text-white">
                    <Film aria-hidden className="size-3.5" />
                  </span>
                ) : null}
              </a>
              <div className="space-y-1 p-2.5 text-sm">
                <p className="truncate font-semibold text-plum" title={item.name}>
                  {item.name || 'Untitled file'}
                </p>
                <p className="truncate text-plum-soft" title={`${item.uploader} from ${item.uploader_ip}`}>
                  {item.uploader || 'Removed person'}, {plural(item.faces, 'face', 'faces')}
                </p>
                {item.status === 'ready' ? null : (
                  <StatusChip tone={item.status === 'failed' ? 'danger' : 'info'} label={item.status === 'failed' ? 'Could not be read' : 'Waiting'} />
                )}
                {item.error ? <p className="text-plum-soft">{item.error}</p> : null}
                <div className="flex flex-wrap gap-1 pt-1">
                  {item.status === 'ready' ? (
                    <MiniButton onClick={() => setAnalysing(item)}>
                      <ScanFace aria-hidden className="size-4" />
                      See analysis
                      <span className="sr-only"> of {item.name}</span>
                    </MiniButton>
                  ) : null}
                  {item.status === 'failed' ? (
                    <MiniButton onClick={() => void retry(item)}>
                      <RotateCcw aria-hidden className="size-4" />
                      Try again
                    </MiniButton>
                  ) : null}
                  <MiniButton variant="quiet" onClick={() => setRemoving(item)}>
                    <Trash2 aria-hidden className="size-4" />
                    Remove
                    <span className="sr-only"> {item.name}</span>
                  </MiniButton>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {query.data ? <Pagination page={page} pageSize={MEDIA_PAGE} total={query.data.total} onPage={setPage} label="Files" busy={query.pending} /> : null}
      {analysing ? <AnalysisDialog mediaId={analysing.id} name={analysing.name} onClose={() => setAnalysing(null)} /> : null}
      {removing ? (
        <ConfirmDialog
          open
          tone="danger"
          title="Remove this file?"
          description={`${removing.name || 'This file'} goes away for everyone, with the faces found in it. This cannot be undone.`}
          confirmLabel="Remove the file"
          busy={busy}
          onConfirm={() => void remove()}
          onClose={() => setRemoving(null)}
        />
      ) : null}
    </Panel>
  )
}

type PersonDialog = { kind: 'rename' | 'merge' | 'remove'; person: AdminGalleryPerson }

function PeoplePanel({ flash, onChanged }: { flash: Flash; onChanged: () => void }) {
  const [typed, setTyped] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const query = useAdminQuery<AdminGalleryPeople>('/admin/gallery/people', { q: search, offset: (page - 1) * PEOPLE_PAGE, limit: PEOPLE_PAGE })
  const [dialog, setDialog] = useState<PersonDialog | null>(null)
  const [name, setName] = useState('')
  const [into, setInto] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(typed.trim())
      setPage(1)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [typed])

  const open = (kind: PersonDialog['kind'], person: AdminGalleryPerson) => {
    setDialog({ kind, person })
    setName(person.name)
    setInto('')
    setProblem(null)
  }

  const confirm = async () => {
    if (!dialog) return
    const path = `/admin/gallery/people/${dialog.person.id}`
    setBusy(true)
    setProblem(null)
    try {
      if (dialog.kind === 'rename') await adminAction(path, { method: 'PATCH', body: { name } })
      if (dialog.kind === 'merge') await adminAction(`${path}/merge`, { body: { into } })
      if (dialog.kind === 'remove') await adminAction(path, { method: 'DELETE' })
      flash.show({ tone: 'success', message: dialog.kind === 'rename' ? 'Name saved.' : dialog.kind === 'merge' ? 'People merged.' : 'Person removed.' })
      setDialog(null)
      void query.reload()
      onChanged()
    } catch (failure) {
      setProblem(isApiError(failure) ? failure.message : describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  const label = (person: AdminGalleryPerson) => person.name || 'Untitled'
  const others = query.data?.people.filter((person) => person.id !== dialog?.person.id) ?? []

  return (
    <Panel title="People" description="Rename someone, join two entries that are the same person, or remove someone's face data. Their photos stay.">
      <div className="relative max-w-sm">
        <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-plum-soft" />
        <input
          type="search"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="Search by name"
          aria-label="Search people by name"
          className="min-h-11 w-full rounded-[var(--radius-control)] border border-sand-strong bg-paper pl-9 pr-3"
        />
      </div>
      {query.error && !query.data ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the people" /> : null}
      {query.data && query.data.people.length === 0 ? <EmptyState title="Nobody here">People appear once they add a selfie, or once a face turns up in two files.</EmptyState> : null}
      {query.data && query.data.people.length > 0 ? (
        <ul className="mt-4 divide-y divide-sand">
          {query.data.people.map((person) => (
            <li key={person.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <span className="w-12 shrink-0">
                <Coin src={person.avatar_url} size="small" />
              </span>
              <span className="min-w-0 flex-1">
                <span className={`block truncate font-semibold ${person.name ? 'text-plum' : 'text-plum-soft'}`}>{label(person)}</span>
                <span className="block text-sm text-plum-soft">{countLabel(person.photos, person.videos)}</span>
              </span>
              <span className="flex flex-wrap gap-1">
                <MiniButton variant="quiet" onClick={() => open('rename', person)}>
                  <UserPen aria-hidden className="size-4" />
                  Rename
                  <span className="sr-only"> {label(person)}</span>
                </MiniButton>
                <MiniButton variant="quiet" onClick={() => open('merge', person)}>
                  Merge
                  <span className="sr-only"> {label(person)}</span>
                </MiniButton>
                <MiniButton variant="quiet" onClick={() => open('remove', person)}>
                  <Trash2 aria-hidden className="size-4" />
                  Remove
                  <span className="sr-only"> {label(person)}</span>
                </MiniButton>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {query.data ? <Pagination page={page} pageSize={PEOPLE_PAGE} total={query.data.total} onPage={setPage} label="People" busy={query.pending} /> : null}

      {dialog?.kind === 'rename' ? (
        <ConfirmDialog open title="Rename this person" confirmLabel="Save name" busy={busy} error={problem} onConfirm={() => void confirm()} onClose={() => setDialog(null)}>
          <InputField label="Name" value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === 'merge' ? (
        <ConfirmDialog
          open
          title={`Merge ${label(dialog.person)} into someone`}
          description="Use this when one person has two entries. All their photos join the person you choose. The list shows the people on this page: search first if you do not see them."
          confirmLabel="Merge"
          confirmDisabled={!into}
          busy={busy}
          error={problem}
          onConfirm={() => void confirm()}
          onClose={() => setDialog(null)}
        >
          <SelectField label="Merge into" value={into} onChange={(event) => setInto(event.target.value)}>
            <option value="">Choose a person</option>
            {others.map((person) => (
              <option key={person.id} value={person.id}>
                {label(person)} ({countLabel(person.photos, person.videos)})
              </option>
            ))}
          </SelectField>
        </ConfirmDialog>
      ) : null}
      {dialog?.kind === 'remove' ? (
        <ConfirmDialog
          open
          tone="danger"
          title={`Remove ${label(dialog.person)}?`}
          description="This deletes their name, picture and every record of their face. Their photos stay in the gallery. This cannot be undone."
          confirmLabel="Remove the person"
          busy={busy}
          error={problem}
          onConfirm={() => void confirm()}
          onClose={() => setDialog(null)}
        >
          <Notice tone="info">Someone can come back later by filling in the form again.</Notice>
        </ConfirmDialog>
      ) : null}
    </Panel>
  )
}
