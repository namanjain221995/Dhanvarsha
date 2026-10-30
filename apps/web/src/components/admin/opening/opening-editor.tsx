'use client'

import { EyeOff, ExternalLink, Plus, RotateCcw, Save } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { newIdempotencyKey } from '@/lib/api'
import { describeError, errorCode, isApiError, plainValidation } from '@/lib/admin/errors'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSignal } from '@/lib/admin/session'
import type { AdminOpening, AdminOpeningCard, OpeningContent } from '@/lib/admin/types'
import { ConfirmDialog } from '../confirm'
import { InputField, SwitchField, TextAreaField } from '../form'
import { FlashRegion, LoadError, MiniButton, PageHeader, PageSkeleton, Panel, useFlash } from '../primitives'
import { CardRow, cardControlId, type CardDraft, type CardErrors } from './card-row'
import { CircleImageField, type ImageRef } from './circle-image-field'
import type { MediaRef } from './media-field'

const PATH = '/admin/opening'
const MAX_CARDS = 40
/** Two rows of seven: what fits on one screen of a projector or the LED wall. */
const ONE_SCREEN = 14
const STALE_MESSAGE = 'Someone else changed this page. Reload to see the latest version.'
/** How often the opening page asks for changes (components/participant/opening-page.tsx). */
const REFRESH_SECONDS = 10

type Draft = {
  title: string
  year: string
  tagline: string
  button_label: string
  hidden: boolean
  logo: ImageRef
  cards: CardDraft[]
}

/**
 * A card's picture or video as the editor shows it. Only GET carries its name and size; after a
 * save the editor keeps what it already knew about the same file.
 */
function mediaOf(card: AdminOpeningCard, known?: Draft): MediaRef {
  const id = card.media_asset_id ?? null
  if (!id) return null
  if (card.media) return { id, ...card.media }
  const before = known?.cards.find((draft) => draft.media?.id === id)?.media
  return before ?? { id, url: '', type: 'image', name: '', byte_size: 0 }
}

function draftFrom(content: OpeningContent, known?: Draft): Draft {
  return {
    title: content.title,
    year: content.year,
    tagline: content.tagline,
    button_label: content.button_label,
    hidden: content.hidden,
    logo: { id: content.logo_asset_id ?? null, url: content.logo_url ?? null },
    cards: content.cards.map((card) => ({
      id: card.id,
      title: card.title,
      icon: card.icon,
      image: { id: card.image_asset_id ?? null, url: card.image_url ?? null },
      link: card.link ?? '',
      media: mediaOf(card, known),
    })),
  }
}

/** The PUT body without the version: the whole page, trimmed as the API stores it. */
function contentOf(draft: Draft) {
  return {
    title: draft.title.trim(),
    year: draft.year.trim(),
    tagline: draft.tagline.trim(),
    button_label: draft.button_label.trim(),
    hidden: draft.hidden,
    logo_asset_id: draft.logo.id,
    cards: draft.cards.map((card) => ({
      id: card.id,
      title: card.title.trim(),
      icon: card.icon,
      image_asset_id: card.image.id,
      link: card.link.trim(),
      media_asset_id: card.media?.id ?? null,
    })),
  }
}

const sameContent = (a: Draft, b: Draft) => JSON.stringify(contentOf(a)) === JSON.stringify(contentOf(b))

/** The links the API accepts: an http(s) address, or a path on this site starting with one "/". */
const validLink = (link: string) => link === '' || /^https?:\/\/[^\s\\/?#]+[^\s\\]*$/i.test(link) || /^\/(?!\/)[^\s\\]*$/.test(link)

/** Card errors are keyed by card id, so they stay on the right card when cards move. */
const cardKey = (cardId: string, field: string) => `card:${cardId}:${field}`

/** The same checks as the API, so most mistakes are caught before saving. */
function check(draft: Draft): Record<string, string> {
  const errors: Record<string, string> = {}
  const c = contentOf(draft)
  if (!c.title) errors.title = 'Give the page a title.'
  if (!c.button_label) errors.button_label = 'Give the buttons a label, for example PLAY GAME.'
  draft.cards.forEach((card, index) => {
    const saved = c.cards[index]!
    if (!saved.title) errors[cardKey(card.id, 'title')] = 'Give this card a title.'
    if (!validLink(saved.link)) {
      errors[cardKey(card.id, 'link')] = 'Use a web address starting with https:// or a path on this site starting with /.'
    }
  })
  return errors
}

/** The API's field errors ("title", "cards.3.link") mapped onto this form. */
function serverErrors(error: unknown, draft: Draft): Record<string, string> {
  if (!isApiError(error)) return {}
  const fields = (error.details?.fields ?? []) as { field?: string; message?: string }[]
  const out: Record<string, string> = {}
  if (!Array.isArray(fields)) return out
  for (const item of fields) {
    if (!item?.field) continue
    const message = plainValidation(item.message ?? 'Check this value.').replace(/^Value error, /, '')
    const card = /^cards\.(\d+)\.(\w+)$/.exec(item.field)
    const cardId = card ? draft.cards[Number(card[1])]?.id : undefined
    if (card && cardId) out[cardKey(cardId, card[2]!)] = message
    else out[item.field] = message
  }
  return out
}

function newCardId(): string {
  // A UUID (or 32 hex digits without a secure context): within the API's 40 letters, digits and dashes.
  return newIdempotencyKey()
}

function focusSoon(id: string, fallback?: string) {
  window.requestAnimationFrame(() => {
    const target = document.getElementById(id) as HTMLButtonElement | HTMLInputElement | null
    if (target && !target.disabled) target.focus()
    else if (fallback) document.getElementById(fallback)?.focus()
  })
}

export function OpeningEditorPage() {
  const query = useAdminQuery<AdminOpening>(PATH)
  const flash = useFlash()
  const [base, setBase] = useState<AdminOpening | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [stale, setStale] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)
  const [confirmHide, setConfirmHide] = useState(false)
  const [hiding, setHiding] = useState(false)
  const [hideError, setHideError] = useState<string | null>(null)

  const server = query.data
  const dirty = draft !== null && base !== null && !sameContent(draft, draftFrom(base.opening))
  // Adopt fresh server data whenever there are no unsaved edits.
  if (server && (base === null || (server !== base && !dirty))) {
    setBase(server)
    setDraft(draftFrom(server.opening))
    setStale(false)
  }

  useAdminSignal((signal) => {
    if (signal.type === 'state' || signal.type === 'resync' || signal.type === 'visible') void query.reload()
  })

  if (!draft || !base) {
    return (
      <>
        <PageHeader title="Opening page" />
        <div className="mt-6">
          {query.error ? (
            <LoadError error={query.error} onRetry={() => void query.reload()} what="the opening page" />
          ) : (
            <PageSkeleton rows={3} />
          )}
        </div>
      </>
    )
  }

  const otherSaved = server !== undefined && server.settings_version !== base.settings_version
  const atDefaults = sameContent(draftFrom(base.opening), draftFrom(base.defaults))

  const clearError = (key: string) => {
    if (!errors[key]) return
    setErrors((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
    clearError(key === 'logo' ? 'logo_asset_id' : key)
  }

  const updateCard = (cardId: string, patch: Partial<CardDraft>) => {
    setDraft((prev) => (prev ? { ...prev, cards: prev.cards.map((card) => (card.id === cardId ? { ...card, ...patch } : card)) } : prev))
    const errorField = (field: string) => (field === 'image' ? 'image_asset_id' : field === 'media' ? 'media_asset_id' : field)
    for (const field of Object.keys(patch)) clearError(cardKey(cardId, errorField(field)))
  }

  const moveCard = (cardId: string, direction: -1 | 1) => {
    const from = draft.cards.findIndex((card) => card.id === cardId)
    const to = from + direction
    if (from < 0 || to < 0 || to >= draft.cards.length) return
    const cards = [...draft.cards]
    const [moved] = cards.splice(from, 1)
    cards.splice(to, 0, moved!)
    setDraft({ ...draft, cards })
    // Focus stays with the card that moved; at the top or bottom, on its other arrow.
    const same = direction < 0 ? 'up' : 'down'
    const other = direction < 0 ? 'down' : 'up'
    focusSoon(cardControlId(cardId, same), cardControlId(cardId, other))
  }

  const deleteCard = (cardId: string) => {
    const index = draft.cards.findIndex((card) => card.id === cardId)
    const cards = draft.cards.filter((card) => card.id !== cardId)
    setDraft({ ...draft, cards })
    const next = cards[index] ?? cards[index - 1]
    focusSoon(next ? cardControlId(next.id, 'delete') : 'opening-add-card')
  }

  const addCard = () => {
    const card: CardDraft = { id: newCardId(), title: '', icon: 'sparkles', image: { id: null, url: null }, link: '', media: null }
    setDraft({ ...draft, cards: [...draft.cards, card] })
    focusSoon(cardControlId(card.id, 'title'))
  }

  const adopt = (updated: AdminOpening) => {
    query.mutate(() => updated)
    setBase(updated)
    setDraft(draftFrom(updated.opening, draft))
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
      const updated = await adminAction<AdminOpening>(PATH, {
        method: 'PUT',
        body: { settings_version: base.settings_version, ...contentOf(draft) },
      })
      adopt(updated)
      flash.show({ tone: 'success', message: `Saved. Screens showing the opening page update within ${REFRESH_SECONDS} seconds.` })
    } catch (error) {
      if (errorCode(error) === 'STALE_SETTINGS') {
        setStale(true)
        flash.clear()
      } else {
        const fields = serverErrors(error, draft)
        setErrors(fields)
        flash.show({
          tone: 'danger',
          title: 'Not saved',
          message: Object.keys(fields).length ? 'Some fields need attention.' : describeError(error),
        })
      }
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setResetting(true)
    setResetError(null)
    try {
      const updated = await adminAction<AdminOpening>(PATH, { method: 'DELETE', body: { settings_version: base.settings_version } })
      adopt(updated)
      setConfirmReset(false)
      flash.show({ tone: 'success', message: 'The opening page is back to the defaults.' })
    } catch (error) {
      if (errorCode(error) === 'STALE_SETTINGS') {
        setConfirmReset(false)
        setStale(true)
        flash.clear()
      } else {
        setResetError(describeError(error))
      }
    } finally {
      setResetting(false)
    }
  }

  /**
   * Every screen turns all cards face down again. The page's version does not change, so unsaved
   * edits here stay saveable; the fresh page is adopted (above) only when there are none.
   */
  const hideAll = async () => {
    setHiding(true)
    setHideError(null)
    try {
      const updated = await adminAction<AdminOpening>(`${PATH}/hide-all`, { method: 'POST' })
      query.mutate(() => updated)
      setConfirmHide(false)
      flash.show({
        tone: 'success',
        message: `All cards are hidden again. Screens showing the opening page follow within ${REFRESH_SECONDS} seconds.`,
      })
    } catch (error) {
      setHideError(describeError(error))
    } finally {
      setHiding(false)
    }
  }

  /** Back to the saved page; after a refused save, fetch the latest and adopt it when it lands. */
  const discard = () => {
    const fresh = server ?? base
    setBase(fresh)
    setDraft(draftFrom(fresh.opening))
    setErrors({})
    if (stale || otherSaved) void query.reload()
    setStale(false)
    flash.clear()
  }

  const cardErrors = (cardId: string): CardErrors => ({
    title: errors[cardKey(cardId, 'title')],
    icon: errors[cardKey(cardId, 'icon')],
    link: errors[cardKey(cardId, 'link')],
    image_asset_id: errors[cardKey(cardId, 'image_asset_id')],
    media_asset_id: errors[cardKey(cardId, 'media_asset_id')],
  })
  const count = draft.cards.length

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
          title="Opening page"
          description="The showcase for the big screen at /opening: the title, the year and a card for each part of the programme. Anyone can open it; nobody signs in."
          actions={
            <>
              <a
                href="/opening"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full border border-plum-faint/60 bg-paper px-3.5 text-sm font-semibold text-plum transition-colors duration-150 hover:border-plum-soft hover:bg-white"
              >
                <ExternalLink aria-hidden className="size-4" />
                Open the page
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
              <MiniButton
                variant="quiet"
                disabled={atDefaults && !dirty}
                title={atDefaults && !dirty ? 'The page already shows the defaults.' : undefined}
                onClick={() => {
                  setResetError(null)
                  setConfirmReset(true)
                }}
              >
                <RotateCcw aria-hidden className="size-4" />
                Reset to defaults
              </MiniButton>
            </>
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
            {stale
              ? 'Your changes were not saved. Reload, then make your change again.'
              : 'Saving now would fail. Reload first; your unsaved edits will be replaced.'}
          </Notice>
        ) : null}

        <Panel
          className="mt-6"
          title="Game-show reveal"
          description="The cards lie face down, each with its number, until someone scratches one open on the screen."
        >
          <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
            <div className="min-w-0 max-w-2xl flex-1 basis-80">
              <SwitchField
                label="Hide cards until they are revealed (scratch card)"
                description="Pick a number, then scratch that card on the projector or a phone to reveal it. Revealed cards stay revealed on that screen. Off shows every card as it is."
                checked={draft.hidden}
                onChange={(hidden) => update('hidden', hidden)}
              />
            </div>
            <div className="py-3">
              <MiniButton
                onClick={() => {
                  setHideError(null)
                  setConfirmHide(true)
                }}
                disabled={!base.opening.hidden}
                title={base.opening.hidden ? undefined : 'The saved page shows its cards. Turn hiding on and save first.'}
              >
                <EyeOff aria-hidden className="size-4" />
                Hide all cards again
              </MiniButton>
              <p className="mt-1 max-w-xs text-sm text-plum-soft">
                {base.opening.hidden
                  ? 'For a new round: every screen turns all its cards face down again.'
                  : 'Available while the saved page hides its cards.'}
              </p>
            </div>
          </div>
        </Panel>

        <div className="mt-6 grid gap-5 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] xl:items-start">
          <Panel title="Header" description="Above the cards, in the middle of the screen.">
            <div className="space-y-5">
              <CircleImageField
                label="Logo"
                kind="programme_logo"
                image={draft.logo}
                size={72}
                fallback={
                  // eslint-disable-next-line @next/next/no-img-element -- a static file from public/
                  <img src="/brand/techsara-mark.png" alt="" className="size-[72%] object-contain" />
                }
                fallbackText="Built-in TechSara mark."
                error={errors.logo_asset_id}
                onChange={(logo) => update('logo', logo)}
                hint="Shown in the gold ring at the top. Square works best. Remove it to show the TechSara mark again."
              />
              <InputField
                label="Title"
                value={draft.title}
                maxLength={80}
                error={errors.title}
                placeholder={base.defaults.title}
                hint="Shown in gold capitals, for example DHANVARSHA."
                onChange={(e) => update('title', e.target.value)}
              />
              <InputField
                label="Year"
                optional
                value={draft.year}
                maxLength={20}
                error={errors.year}
                hint="Under the title, between two gold lines."
                onChange={(e) => update('year', e.target.value)}
              />
              <TextAreaField
                label="Tagline"
                optional
                rows={3}
                maxLength={300}
                value={draft.tagline}
                error={errors.tagline}
                hint="One short line reads best on the big screen."
                onChange={(e) => update('tagline', e.target.value)}
              />
              <InputField
                label="Button text"
                value={draft.button_label}
                maxLength={30}
                error={errors.button_label}
                hint="Shown on every card's button, e.g. PLAY GAME"
                onChange={(e) => update('button_label', e.target.value)}
              />
            </div>
          </Panel>

          <Panel
            title="Cards"
            description={
              <>
                Shown in this order, {count === 1 ? '1 card' : `${count} cards`}. Up to {ONE_SCREEN} fit on one screen of a projector or the
                LED wall; with more, the page scrolls. A card&apos;s button shows its picture or video full screen, or else goes to its
                link (without either it is decoration only). A picture in the circle replaces the icon.
              </>
            }
          >
            {count ? (
              <ol className="space-y-3">
                {draft.cards.map((card, index) => (
                  <CardRow
                    key={card.id}
                    card={card}
                    position={index}
                    count={count}
                    icons={base.icons}
                    errors={cardErrors(card.id)}
                    onChange={(patch) => updateCard(card.id, patch)}
                    onMove={(direction) => moveCard(card.id, direction)}
                    onDelete={() => deleteCard(card.id)}
                  />
                ))}
              </ol>
            ) : (
              <p className="rounded-2xl border border-dashed border-sand-strong bg-ivory px-4 py-5 text-sm text-plum-soft">
                No cards. The page shows only the header.
              </p>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <MiniButton id="opening-add-card" onClick={addCard} disabled={count >= MAX_CARDS}>
                <Plus aria-hidden className="size-4" />
                Add card
              </MiniButton>
              {count >= MAX_CARDS ? <p className="text-sm text-plum-soft">A page holds up to {MAX_CARDS} cards.</p> : null}
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
        open={confirmReset}
        title="Reset the opening page?"
        description="The title, year, tagline, button text, logo, every card and the scratch-card setting go back to the built-in defaults, on every screen showing the page. Unsaved changes here are lost too."
        confirmLabel="Reset to defaults"
        tone="danger"
        busy={resetting}
        error={resetError}
        onConfirm={() => void reset()}
        onClose={() => setConfirmReset(false)}
      />
      <ConfirmDialog
        open={confirmHide}
        title="Hide all cards again?"
        description={`Every screen showing the opening page hides all cards again within ${REFRESH_SECONDS} seconds.`}
        confirmLabel="Hide all cards"
        busy={hiding}
        error={hideError}
        onConfirm={() => void hideAll()}
        onClose={() => setConfirmHide(false)}
      />
    </>
  )
}
