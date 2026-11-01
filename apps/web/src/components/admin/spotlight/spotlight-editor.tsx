'use client'

import { ArrowDown, ArrowUp, CornerDownLeft, ExternalLink, EyeOff, Plus, RotateCcw, Save, Sparkles, Trash2, UserRound } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { newIdempotencyKey } from '@/lib/api'
import { describeError, errorCode, isApiError, plainValidation } from '@/lib/admin/errors'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSignal } from '@/lib/admin/session'
import type { AdminSpotlight, AdminSpotlightEntry } from '@/lib/admin/types'
import { ConfirmDialog } from '../confirm'
import { InputField, SelectField } from '../form'
import { EmptyState, FlashRegion, IconButton, LoadError, MiniButton, PageHeader, PageSkeleton, Panel, StatusChip, useFlash } from '../primitives'
import { PhotoField, type PhotoRef } from './photo-field'

/*
 * The spotlight (/admin/spotlight): the people the big screen at /spotlight reveals one at a
 * time. Above: the live buttons. "Show" brings that person's card to the middle of every screen
 * showing /spotlight, where it scratches itself open; "Send back" puts it back early; "Hide all
 * again" turns every card face down. Below: the title, the hold time and the people (a picture,
 * a name and an optional line under it), saved together.
 */

const PATH = '/admin/spotlight'
const MAX_PEOPLE = 60
const STALE_MESSAGE = 'Someone else changed the spotlight. Reload to see the latest version.'
/** Other organisers' Show and Hide buttons reach this page within this many seconds. */
const POLL_MS = 4000
const HOLD_CHOICES = [5, 8, 10, 12, 15, 20, 30, 45, 60, 90, 0]

type Response = { spotlight: AdminSpotlight }
type PersonDraft = { id: string; name: string; detail: string; photo: PhotoRef }
type Draft = { title: string; subtitle: string; hold_seconds: number; people: PersonDraft[] }

const holdLabel = (seconds: number) => (seconds === 0 ? 'Until I send it back' : `${seconds} seconds`)

/** A person's picture as the editor shows it; after a save the editor keeps what it knew. */
function photoOf(entry: AdminSpotlightEntry, known?: Draft): PhotoRef {
  const id = entry.image_asset_id ?? null
  if (!id) return null
  if (entry.image) return { id, ...entry.image }
  return known?.people.find((person) => person.photo?.id === id)?.photo ?? { id, url: '', name: '', byte_size: 0, width: 0, height: 0 }
}

function draftFrom(spotlight: AdminSpotlight, known?: Draft): Draft {
  return {
    title: spotlight.title,
    subtitle: spotlight.subtitle,
    hold_seconds: spotlight.hold_seconds,
    people: spotlight.entries.map((entry) => ({ id: entry.id, name: entry.name, detail: entry.detail, photo: photoOf(entry, known) })),
  }
}

/** The PUT body without the version, trimmed as the API stores it. */
function contentOf(draft: Draft) {
  return {
    title: draft.title.trim(),
    subtitle: draft.subtitle.trim(),
    hold_seconds: draft.hold_seconds,
    entries: draft.people.map((person) => ({
      id: person.id,
      name: person.name.trim(),
      detail: person.detail.trim(),
      image_asset_id: person.photo?.id ?? null,
    })),
  }
}

const sameContent = (a: Draft, b: Draft) => JSON.stringify(contentOf(a)) === JSON.stringify(contentOf(b))

/** Person errors are keyed by id, so they stay on the right person when people move. */
const personKey = (personId: string, field: string) => `person:${personId}:${field}`
const controlId = (personId: string, part: string) => `spotlight-${personId}-${part}`

function check(draft: Draft): Record<string, string> {
  const errors: Record<string, string> = {}
  const content = contentOf(draft)
  if (!content.title) errors.title = 'Give the screen a title, for example PROFESSIONAL HONOURS.'
  draft.people.forEach((person, index) => {
    if (!content.entries[index]!.name) errors[personKey(person.id, 'name')] = 'Type the name shown on the card.'
  })
  return errors
}

/** The API's field errors ("title", "entries.3.name") mapped onto this form. */
function serverErrors(error: unknown, draft: Draft): Record<string, string> {
  if (!isApiError(error)) return {}
  const fields = (error.details?.fields ?? []) as { field?: string; message?: string }[]
  const out: Record<string, string> = {}
  if (!Array.isArray(fields)) return out
  for (const item of fields) {
    if (!item?.field) continue
    const message = plainValidation(item.message ?? 'Check this value.').replace(/^Value error, /, '')
    const match = /^entries\.(\d+)\.(\w+)$/.exec(item.field)
    const personId = match ? draft.people[Number(match[1])]?.id : undefined
    if (match && personId) out[personKey(personId, match[2] === 'image_asset_id' ? 'photo' : match[2]!)] = message
    else out[item.field] = message
  }
  return out
}

function focusSoon(id: string, fallback?: string) {
  window.requestAnimationFrame(() => {
    const target = document.getElementById(id) as HTMLButtonElement | HTMLInputElement | null
    if (target && !target.disabled) target.focus()
    else if (fallback) document.getElementById(fallback)?.focus()
  })
}

function PersonRow({
  person,
  position,
  count,
  errors,
  onChange,
  onMove,
  onDelete,
}: {
  person: PersonDraft
  position: number
  count: number
  errors: { name?: string; detail?: string; photo?: string }
  onChange: (patch: Partial<PersonDraft>) => void
  onMove: (direction: -1 | 1) => void
  onDelete: () => void
}) {
  const id = useId()
  const number = position + 1
  return (
    <li className="rounded-2xl border border-sand bg-paper px-4 pb-4 pt-3 sm:px-5">
      <div role="group" aria-labelledby={`${id}-label`}>
        <div className="flex items-center gap-3">
          <p id={`${id}-label`} className="min-w-0 flex-1 truncate text-sm font-bold text-plum">
            Card {number}
            <span className="font-normal text-plum-soft">{person.name.trim() ? `: ${person.name.trim()}` : ''}</span>
          </p>
          <div className="flex shrink-0 gap-0.5">
            <IconButton id={controlId(person.id, 'up')} label={`Move card ${number} up`} disabled={position === 0} onClick={() => onMove(-1)}>
              <ArrowUp aria-hidden className="size-[1.1rem]" />
            </IconButton>
            <IconButton
              id={controlId(person.id, 'down')}
              label={`Move card ${number} down`}
              disabled={position === count - 1}
              onClick={() => onMove(1)}
            >
              <ArrowDown aria-hidden className="size-[1.1rem]" />
            </IconButton>
            <IconButton
              id={controlId(person.id, 'delete')}
              label={`Remove card ${number}`}
              className="hover:bg-danger-wash hover:text-danger"
              onClick={onDelete}
            >
              <Trash2 aria-hidden className="size-[1.1rem]" />
            </IconButton>
          </div>
        </div>
        <div className="mt-3 grid gap-4 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,1fr)] md:items-start">
          <PhotoField photo={person.photo} name={person.name} error={errors.photo} onChange={(photo) => onChange({ photo })} />
          <InputField
            id={controlId(person.id, 'name')}
            label="Name"
            value={person.name}
            maxLength={80}
            error={errors.name}
            placeholder="e.g. Asha Mehta"
            onChange={(e) => onChange({ name: e.target.value })}
          />
          <InputField
            label="Line under the name"
            optional
            value={person.detail}
            maxLength={120}
            error={errors.detail}
            placeholder="e.g. Recruiter of the Year"
            onChange={(e) => onChange({ detail: e.target.value })}
          />
        </div>
      </div>
    </li>
  )
}

/** The live buttons: one tile per saved person, plus Send back and Hide all again. */
function LivePanel({
  live,
  dirty,
  busy,
  onShow,
  onBack,
  onHideAll,
}: {
  live: AdminSpotlight
  dirty: boolean
  busy: string | null
  onShow: (entry: AdminSpotlightEntry) => void
  onBack: () => void
  onHideAll: () => void
}) {
  const revealed = new Set(live.revealed)
  const out = live.shown && !live.shown.back ? live.shown.id : null
  const hold =
    live.hold_seconds === 0
      ? 'A shown card stays in the middle until you send it back or show someone else.'
      : `A shown card stays in the middle for ${live.hold_seconds} seconds, then goes back to its place face up.`
  return (
    <Panel
      className="mt-6"
      title="On the big screen"
      description={
        <>
          Press <strong>Show</strong> and, on every screen showing the spotlight, that card comes to the front, scratches itself open and
          shows the picture and the name. {hold}
        </>
      }
      actions={
        <>
          <MiniButton onClick={onBack} busy={busy === 'back'} disabled={!out || busy !== null}>
            <CornerDownLeft aria-hidden className="size-4" />
            Send back
          </MiniButton>
          <MiniButton onClick={onHideAll} disabled={(live.revealed.length === 0 && !live.shown) || busy !== null}>
            <EyeOff aria-hidden className="size-4" />
            Hide all again
          </MiniButton>
        </>
      }
    >
      {dirty ? (
        <Notice tone="warning" className="mb-4" title="You have unsaved changes">
          These buttons use the saved people. Save first to show someone new.
        </Notice>
      ) : null}
      {live.entries.length ? (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {live.entries.map((entry, index) => {
            const showing = out === entry.id
            const isRevealed = revealed.has(entry.id)
            return (
              <li
                key={entry.id}
                className={`flex items-center gap-3 rounded-2xl border px-3 py-2.5 ${showing ? 'border-gold bg-gold-wash' : 'border-sand bg-paper'}`}
              >
                <span className="relative h-16 w-12 shrink-0 overflow-hidden rounded-lg border border-gold/60 bg-[#0c1d5e]">
                  {entry.image?.url ? (
                    // eslint-disable-next-line @next/next/no-img-element -- re-encoded WebP from the API
                    <img src={entry.image.url} alt="" className="absolute inset-0 size-full object-cover" style={{ objectPosition: '50% 22%' }} />
                  ) : (
                    <UserRound aria-hidden className="absolute left-1/2 top-1/2 size-5 -translate-x-1/2 -translate-y-1/2 text-gold/70" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold text-plum">
                    {index + 1}. {entry.name}
                  </span>
                  {entry.detail ? <span className="block truncate text-sm text-plum-soft">{entry.detail}</span> : null}
                  <span className="mt-1 block">
                    {showing ? (
                      <StatusChip tone="live" label="On the screen now" />
                    ) : isRevealed ? (
                      <StatusChip tone="success" label="Revealed" />
                    ) : (
                      <StatusChip tone="neutral" label="Face down" />
                    )}
                  </span>
                </span>
                <MiniButton
                  variant={isRevealed ? 'secondary' : 'primary'}
                  busy={busy === `show:${entry.id}`}
                  disabled={busy !== null}
                  onClick={() => onShow(entry)}
                >
                  <Sparkles aria-hidden className="size-4" />
                  {isRevealed ? 'Show again' : 'Show'}
                  <span className="sr-only"> {entry.name}</span>
                </MiniButton>
              </li>
            )
          })}
        </ul>
      ) : (
        <EmptyState title="Nobody to show yet">Add people below and save. Their cards then appear on the screen, face down.</EmptyState>
      )}
    </Panel>
  )
}

export function SpotlightEditorPage() {
  const query = useAdminQuery<Response>(PATH)
  const flash = useFlash()
  const [base, setBase] = useState<AdminSpotlight | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  // The live state (revealed, shown) follows every answer, unsaved edits or not.
  const [live, setLive] = useState<AdminSpotlight | null>(null)
  const [seen, setSeen] = useState<AdminSpotlight | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmHide, setConfirmHide] = useState(false)
  const [hideError, setHideError] = useState<string | null>(null)

  const server = query.data?.spotlight
  const dirty = draft !== null && base !== null && !sameContent(draft, draftFrom(base))
  if (server && server !== seen) {
    setSeen(server)
    setLive(server)
  }
  // Adopt fresh server content whenever there are no unsaved edits.
  if (server && (base === null || (server !== base && !dirty))) {
    setBase(server)
    setDraft(draftFrom(server, draft ?? undefined))
    setStale(false)
  }

  const { reload } = query
  useAdminSignal((signal) => {
    if (signal.type === 'resync' || signal.type === 'visible') void reload()
  })
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [reload])

  if (!draft || !base || !live) {
    return (
      <>
        <PageHeader title="Spotlight" />
        <div className="mt-6">
          {query.error ? <LoadError error={query.error} onRetry={() => void reload()} what="the spotlight" /> : <PageSkeleton rows={3} />}
        </div>
      </>
    )
  }

  const otherSaved = server !== undefined && server.version !== base.version

  const clearError = (key: string) => {
    if (!errors[key]) return
    setErrors((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const update = <K extends 'title' | 'subtitle' | 'hold_seconds'>(key: K, value: Draft[K]) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
    clearError(key)
  }

  const updatePerson = (personId: string, patch: Partial<PersonDraft>) => {
    setDraft((prev) => (prev ? { ...prev, people: prev.people.map((p) => (p.id === personId ? { ...p, ...patch } : p)) } : prev))
    for (const field of Object.keys(patch)) clearError(personKey(personId, field))
  }

  const movePerson = (personId: string, direction: -1 | 1) => {
    const from = draft.people.findIndex((p) => p.id === personId)
    const to = from + direction
    if (from < 0 || to < 0 || to >= draft.people.length) return
    const people = [...draft.people]
    const [moved] = people.splice(from, 1)
    people.splice(to, 0, moved!)
    setDraft({ ...draft, people })
    focusSoon(controlId(personId, direction < 0 ? 'up' : 'down'), controlId(personId, direction < 0 ? 'down' : 'up'))
  }

  const deletePerson = (personId: string) => {
    const index = draft.people.findIndex((p) => p.id === personId)
    const people = draft.people.filter((p) => p.id !== personId)
    setDraft({ ...draft, people })
    const next = people[index] ?? people[index - 1]
    focusSoon(next ? controlId(next.id, 'delete') : 'spotlight-add')
  }

  const addPerson = () => {
    const person: PersonDraft = { id: newIdempotencyKey(), name: '', detail: '', photo: null }
    setDraft({ ...draft, people: [...draft.people, person] })
    focusSoon(controlId(person.id, 'name'))
  }

  const adopt = (updated: AdminSpotlight) => {
    query.mutate(() => ({ spotlight: updated }))
    setBase(updated)
    setDraft(draftFrom(updated, draft))
    setLive(updated)
    setSeen(updated)
    setErrors({})
    setStale(false)
  }

  const save = async () => {
    const local = check(draft)
    if (Object.keys(local).length) {
      setErrors(local)
      flash.show({ tone: 'danger', message: 'Some fields need attention before saving.' })
      return
    }
    setSaving(true)
    setErrors({})
    try {
      const updated = await adminAction<Response>(PATH, { method: 'PUT', body: { version: base.version, ...contentOf(draft) } })
      adopt(updated.spotlight)
      flash.show({ tone: 'success', message: 'Saved. Screens showing the spotlight update within a second.' })
    } catch (error) {
      if (errorCode(error) === 'STALE_SPOTLIGHT') {
        setStale(true)
        flash.clear()
      } else {
        const fields = serverErrors(error, draft)
        setErrors(fields)
        flash.show({ tone: 'danger', title: 'Not saved', message: Object.keys(fields).length ? 'Some fields need attention.' : describeError(error) })
      }
    } finally {
      setSaving(false)
    }
  }

  /** A live button. Its answer is the latest state; unsaved edits here stay as they are. */
  const act = async (key: string, path: string, body?: Record<string, unknown>) => {
    setBusy(key)
    try {
      const updated = await adminAction<Response>(`${PATH}${path}`, { method: 'POST', body })
      setLive(updated.spotlight)
      setSeen(updated.spotlight)
      query.mutate(() => updated)
      return true
    } catch (error) {
      flash.show({ tone: 'danger', title: 'Not done', message: describeError(error) })
      return false
    } finally {
      setBusy(null)
    }
  }

  const show = async (entry: AdminSpotlightEntry) => {
    if (await act(`show:${entry.id}`, '/show', { entry_id: entry.id })) {
      flash.show({ tone: 'success', message: `${entry.name} is on the big screen now.` })
    }
  }

  const sendBack = async () => {
    if (await act('back', '/back')) flash.show({ tone: 'success', message: 'The card is going back to its place.' })
  }

  const hideAll = async () => {
    setHideError(null)
    setBusy('hide')
    try {
      const updated = await adminAction<Response>(`${PATH}/hide-all`, { method: 'POST' })
      setLive(updated.spotlight)
      setSeen(updated.spotlight)
      query.mutate(() => updated)
      setConfirmHide(false)
      flash.show({ tone: 'success', message: 'Every card is face down again on the big screen.' })
    } catch (error) {
      setHideError(describeError(error))
    } finally {
      setBusy(null)
    }
  }

  const discard = () => {
    const fresh = server ?? base
    setBase(fresh)
    setDraft(draftFrom(fresh))
    setErrors({})
    if (stale || otherSaved) void reload()
    setStale(false)
    flash.clear()
  }

  const count = draft.people.length
  const holdChoices = HOLD_CHOICES.includes(draft.hold_seconds) ? HOLD_CHOICES : [draft.hold_seconds, ...HOLD_CHOICES]

  return (
    <>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (dirty) void save()
        }}
      >
        <PageHeader
          title="Spotlight"
          description={
            <>
              Reveal people one at a time on the big screen: each card scratches itself open to show a picture and a name. On the
              opening page, give a card the link <code>/spotlight</code> (in <strong>Opening page</strong>): its button turns every card
              over and deals these cards in their place. The same cards are also at /spotlight on their own. The screen names only
              people you have shown.
            </>
          }
          actions={
            <a
              href="/spotlight"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full border border-plum-faint/60 bg-paper px-3.5 text-sm font-semibold text-plum transition-colors duration-150 hover:border-plum-soft hover:bg-white"
            >
              <ExternalLink aria-hidden className="size-4" />
              Open the screen
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          }
        />
        <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />
        {stale || otherSaved ? (
          <Notice
            tone="warning"
            role="alert"
            className="mt-5"
            title={STALE_MESSAGE}
            action={
              <Button type="button" variant="secondary" onClick={discard}>
                <RotateCcw aria-hidden className="size-4" />
                Reload
              </Button>
            }
          >
            {stale ? 'Your changes were not saved. Reload, then make your change again.' : 'Saving now would fail. Reload first; your unsaved edits will be replaced.'}
          </Notice>
        ) : null}

        <LivePanel
          live={live}
          dirty={dirty}
          busy={busy}
          onShow={(entry) => void show(entry)}
          onBack={() => void sendBack()}
          onHideAll={() => {
            setHideError(null)
            setConfirmHide(true)
          }}
        />

        <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] xl:items-start">
          <Panel title="Screen" description="Above the cards, in the middle of the screen, with the opening page's emblem.">
            <div className="space-y-5">
              <InputField
                label="Title"
                value={draft.title}
                maxLength={80}
                error={errors.title}
                hint="Shown in gold capitals, for example PROFESSIONAL HONOURS."
                onChange={(e) => update('title', e.target.value)}
              />
              <InputField
                label="Line under the title"
                optional
                value={draft.subtitle}
                maxLength={160}
                error={errors.subtitle}
                hint="One short line, for example Dhanvarsha 2026."
                onChange={(e) => update('subtitle', e.target.value)}
              />
              <SelectField
                label="Time in the middle"
                value={String(draft.hold_seconds)}
                error={errors.hold_seconds}
                hint="How long a revealed card stays big before it goes back to its place."
                onChange={(e) => update('hold_seconds', Number(e.target.value))}
              >
                {holdChoices.map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {holdLabel(seconds)}
                  </option>
                ))}
              </SelectField>
            </div>
          </Panel>

          <Panel
            title="People"
            description={
              <>
                One card each, in this order: {count === 1 ? '1 person' : `${count} people`}. Portrait pictures look best; faces near the top
                stay in view.
              </>
            }
          >
            {count ? (
              <ol className="space-y-3">
                {draft.people.map((person, index) => (
                  <PersonRow
                    key={person.id}
                    person={person}
                    position={index}
                    count={count}
                    errors={{
                      name: errors[personKey(person.id, 'name')],
                      detail: errors[personKey(person.id, 'detail')],
                      photo: errors[personKey(person.id, 'photo')],
                    }}
                    onChange={(patch) => updatePerson(person.id, patch)}
                    onMove={(direction) => movePerson(person.id, direction)}
                    onDelete={() => deletePerson(person.id)}
                  />
                ))}
              </ol>
            ) : (
              <p className="rounded-2xl border border-dashed border-sand-strong bg-ivory px-4 py-5 text-sm text-plum-soft">
                Nobody yet. Add a person for each card.
              </p>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <MiniButton id="spotlight-add" onClick={addPerson} disabled={count >= MAX_PEOPLE}>
                <Plus aria-hidden className="size-4" />
                Add person
              </MiniButton>
              {count >= MAX_PEOPLE ? <p className="text-sm text-plum-soft">The spotlight holds up to {MAX_PEOPLE} people.</p> : null}
            </div>
          </Panel>
        </div>

        <div
          className={`sticky bottom-0 z-20 -mx-4 mt-6 border-t border-sand bg-ivory/95 px-4 py-3 backdrop-blur-sm sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10 safe-bottom ${
            dirty ? '' : 'hidden'
          }`}
        >
          <div className="mx-auto flex max-w-[78rem] flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-semibold text-plum" role="status">
              You have unsaved changes
            </p>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" onClick={discard} disabled={saving}>
                Discard
              </Button>
              <Button type="submit" busy={saving} disabled={stale || otherSaved}>
                {saving ? null : <Save aria-hidden className="size-4" />}
                Save changes
              </Button>
            </div>
          </div>
        </div>
      </form>

      {/* Outside the form: the dialog's buttons must never submit it. */}
      <ConfirmDialog
        open={confirmHide}
        title="Hide all cards again?"
        description="Every screen showing the spotlight turns all cards face down at once. The people stay as they are, ready to be shown again."
        confirmLabel="Hide all cards"
        busy={busy === 'hide'}
        error={hideError}
        onConfirm={() => void hideAll()}
        onClose={() => setConfirmHide(false)}
      />
    </>
  )
}
