'use client'

import { Check, RotateCcw, Save } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError, errorCode, fieldErrors } from '@/lib/admin/errors'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import { useAdminSession, useAdminSignal } from '@/lib/admin/session'
import type { Accent, ProgrammeSettings, RunMode } from '@/lib/admin/types'
import { LOBBY_WELCOME } from '@/lib/lobby-welcome'
import {
  canonicalTimeZone,
  formatWhen,
  isoToZonedInput,
  isValidTimeZone,
  timeZoneNames,
  zonedInputToIso,
  zoneLabel,
} from '@/lib/admin/zoned'
import { ImageField, InputField, RadioCards, SwitchField, TextAreaField, ZonedDateTimeField } from '../form'
import { FlashRegion, LoadError, PageHeader, PageSkeleton, Panel, StatusChip, useFlash } from '../primitives'

const ACCENT_SWATCH: Record<Accent, string> = {
  violet: '#5b34c4',
  plum: '#7a2a6e',
  indigo: '#3b43b8',
  teal: '#136f6a',
  rose: '#b02a55',
  amber: '#8a5a00',
  gold: 'conic-gradient(from 210deg, #fff0b8, #dba62d, #8a6410, #f4cb52, #fff0b8)',
}
const ACCENT_LABEL: Partial<Record<Accent, string>> = { gold: 'Gold with coin rain' }

type Draft = {
  name: string
  description: string
  instructions: string
  help_contact: string
  welcome_title: string
  welcome_message: string
  logo: { id: string | null; url: string | null }
  banner: { id: string | null; url: string | null }
  accent: Accent
  timezone: string
  opens: string
  closes: string
  countdown_enabled: boolean
  run_mode: RunMode
  preview_upcoming: boolean
  publish_schedule: boolean
}

function draftFrom(s: ProgrammeSettings): Draft {
  return {
    name: s.name,
    description: s.description,
    instructions: s.instructions,
    help_contact: s.help_contact,
    welcome_title: s.welcome_title,
    welcome_message: s.welcome_message,
    logo: { id: s.logo_asset_id, url: s.logo_url },
    banner: { id: s.banner_asset_id, url: s.banner_url },
    accent: s.accent,
    timezone: s.timezone,
    opens: isoToZonedInput(s.login_opens_at, s.timezone),
    closes: isoToZonedInput(s.login_closes_at, s.timezone),
    countdown_enabled: s.countdown_enabled,
    run_mode: s.run_mode,
    preview_upcoming: s.preview_upcoming,
    publish_schedule: s.publish_schedule,
  }
}

const sameInstant = (a: string | null, b: string | null) => (a && b ? Date.parse(a) === Date.parse(b) : a === b)

/** Fields that changed, as a PATCH body. Errors are client-side checks that block saving. */
function buildPatch(draft: Draft, base: ProgrammeSettings) {
  const errors: Record<string, string> = {}
  const patch: Record<string, unknown> = { settings_version: base.settings_version }
  const text = ['name', 'description', 'instructions', 'help_contact', 'welcome_title', 'welcome_message'] as const
  for (const key of text) if (draft[key].trim() !== base[key]) patch[key] = draft[key].trim()
  if (!draft.name.trim()) errors.name = 'Give the programme a name.'
  if (draft.logo.id !== base.logo_asset_id) {
    if (draft.logo.id) patch.logo_asset_id = draft.logo.id
    else patch.clear_logo = true
  }
  if (draft.banner.id !== base.banner_asset_id) {
    if (draft.banner.id) patch.banner_asset_id = draft.banner.id
    else patch.clear_banner = true
  }
  if (draft.accent !== base.accent) patch.accent = draft.accent
  const zone = canonicalTimeZone(draft.timezone.trim())
  if (!zone) errors.timezone = 'Use an IANA timezone name such as Asia/Kolkata or Europe/London.'
  else if (zone !== base.timezone) patch.timezone = zone
  for (const key of ['countdown_enabled', 'preview_upcoming', 'publish_schedule', 'run_mode'] as const) {
    if (draft[key] !== base[key]) patch[key] = draft[key]
  }
  if (zone) {
    const opens = draft.opens ? zonedInputToIso(draft.opens, zone) : null
    const closes = draft.closes ? zonedInputToIso(draft.closes, zone) : null
    if (draft.opens && !opens) errors.opens = 'Enter a complete date and time.'
    if (draft.closes && !closes) errors.closes = 'Enter a complete date and time.'
    if (opens && closes && Date.parse(closes) <= Date.parse(opens)) errors.closes = 'Access must close after it opens.'
    if (!errors.opens && !errors.closes) {
      if (!opens && !closes) {
        if (base.login_opens_at || base.login_closes_at) patch.clear_login_window = true
      } else {
        if (!sameInstant(opens, base.login_opens_at)) patch.login_opens_at = opens
        if (!sameInstant(closes, base.login_closes_at)) patch.login_closes_at = closes
      }
    }
  }
  const changed = Object.keys(patch).filter((key) => key !== 'settings_version')
  return { patch, errors, changed }
}

export function ProgrammeSettingsPage() {
  const { refreshMe } = useAdminSession()
  const query = useAdminQuery<ProgrammeSettings>('/admin/programme')
  const flash = useFlash()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [base, setBase] = useState<ProgrammeSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [stale, setStale] = useState(false)
  const [zones] = useState(() => timeZoneNames())

  const server = query.data
  const dirty = draft !== null && base !== null && buildPatch(draft, base).changed.length > 0
  // Adopt fresh server data whenever there are no unsaved edits.
  if (server && (base === null || (server !== base && !dirty))) {
    setBase(server)
    setDraft(draftFrom(server))
    setStale(false)
  }

  useAdminSignal((signal) => {
    if (signal.type === 'state' || signal.type === 'resync' || signal.type === 'visible') void query.reload()
  })

  if (!draft || !base) {
    return (
      <>
        <PageHeader title="Programme settings" />
        <div className="mt-6">
          {query.error ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the settings" /> : <PageSkeleton rows={3} />}
        </div>
      </>
    )
  }

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
    if (errors[key]) {
      setErrors((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  const zoneValid = isValidTimeZone(draft.timezone.trim())
  const zone = zoneValid ? (canonicalTimeZone(draft.timezone.trim()) ?? base.timezone) : base.timezone
  const zoneChanged = zoneValid && zone !== base.timezone
  const otherSaved = server !== undefined && server.settings_version !== base.settings_version

  const save = async () => {
    const { patch, errors: localErrors, changed } = buildPatch(draft, base)
    if (Object.keys(localErrors).length) {
      setErrors(localErrors)
      flash.show({ tone: 'danger', message: 'Some fields need attention before saving.' })
      return
    }
    if (!changed.length) return
    setSaving(true)
    setErrors({})
    try {
      const updated = await adminAction<ProgrammeSettings>('/admin/programme', { method: 'PATCH', body: patch })
      query.mutate(() => updated)
      setBase(updated)
      setDraft(draftFrom(updated))
      flash.show({ tone: 'success', message: 'Settings saved. Participants see the changes straight away.' })
      void refreshMe()
    } catch (error) {
      if (errorCode(error) === 'STALE_SETTINGS') {
        setStale(true)
        flash.clear()
      } else {
        const fields = fieldErrors(error)
        const message = describeError(error)
        if (/close after it opens|access closes|closing time/i.test(message)) fields.closes = message
        else if (/timezone/i.test(message)) fields.timezone = message
        setErrors(fields)
        flash.show({ tone: 'danger', title: 'Settings not saved', message })
      }
    } finally {
      setSaving(false)
    }
  }

  const discard = () => {
    const fresh = server ?? base
    setBase(fresh)
    setDraft(draftFrom(fresh))
    setErrors({})
    // After a refused save the cached copy may be the old one: fetch, and adopt it when it lands.
    if (stale) void query.reload()
    setStale(false)
    flash.clear()
  }

  const phaseChip =
    base.login_phase === 'open' ? (
      <StatusChip tone="success" label="Access open now" />
    ) : base.login_phase === 'before_open' ? (
      <StatusChip tone="neutral" label="Access not open yet" />
    ) : (
      <StatusChip tone="warning" label="Access closed" />
    )

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <PageHeader
        title="Programme settings"
        description="What participants see, when they can sign in, and how events open."
      />
      <FlashRegion flash={flash.flash} onDismiss={flash.clear} className="mt-5 empty:hidden" />
      {stale || otherSaved ? (
        <Notice
          tone="warning"
          role="alert"
          className="mt-5"
          title="Another organiser changed these settings"
          action={
            <Button type="button" variant="secondary" onClick={discard}>
              <RotateCcw aria-hidden className="size-4" />
              Load their version
            </Button>
          }
        >
          {stale
            ? 'Your changes were not saved. Load the latest settings, then make your change again.'
            : 'Saving now would fail. Load the latest settings first; your unsaved edits will be replaced.'}
        </Notice>
      ) : null}

      <div className="mt-6 grid gap-5 xl:grid-cols-2 xl:items-start">
        <div className="min-w-0 space-y-5">
          <Panel title="Basics" description="Shown at the top of the participant programme.">
            <div className="space-y-5">
              <InputField
                label="Programme name"
                value={draft.name}
                maxLength={120}
                onChange={(e) => update('name', e.target.value)}
                error={errors.name}
                required
              />
              <TextAreaField
                label="Description"
                optional
                rows={3}
                maxLength={4000}
                value={draft.description}
                onChange={(e) => update('description', e.target.value)}
                error={errors.description}
              />
              <TextAreaField
                label="Instructions for participants"
                optional
                rows={3}
                maxLength={4000}
                value={draft.instructions}
                onChange={(e) => update('instructions', e.target.value)}
                hint="For example: keep this page open; each round appears here when it opens."
                error={errors.instructions}
              />
              <InputField
                label="Where to get help"
                optional
                maxLength={200}
                value={draft.help_contact}
                onChange={(e) => update('help_contact', e.target.value)}
                hint="Shown when a participant is stuck, for example “the help desk beside the stage”."
                error={errors.help_contact}
              />
            </div>
          </Panel>

          <Panel
            title="Lobby welcome"
            description="What phones show at the top of the lobby while no round is open. With a welcome, the card shows only your text."
          >
            <div className="space-y-5">
              <InputField
                label="Welcome heading"
                optional
                maxLength={120}
                value={draft.welcome_title}
                placeholder={LOBBY_WELCOME.title}
                onChange={(e) => update('welcome_title', e.target.value)}
                hint={`For example “Welcome to ${draft.name.trim() || 'the programme'}”. With both boxes empty, phones show “${LOBBY_WELCOME.title}” and the next round.`}
                error={errors.welcome_title}
              />
              <TextAreaField
                label="Welcome message"
                optional
                rows={3}
                maxLength={1000}
                value={draft.welcome_message}
                onChange={(e) => update('welcome_message', e.target.value)}
                hint="A line or two under the heading, for example where to sit or when the first round starts."
                error={errors.welcome_message}
              />
            </div>
          </Panel>

          <Panel title="Look" description="Logo, banner and the accent colour of the participant pages.">
            <div className="space-y-6">
              <ImageField
                label="Logo"
                kind="programme_logo"
                url={draft.logo.url}
                name={draft.name}
                onUploaded={(asset) => update('logo', { id: asset.id, url: asset.url })}
                onRemove={() => update('logo', { id: null, url: null })}
                hint="Square works best. JPEG, PNG, WebP or GIF, up to 5 MB. Without a logo, participants see the initials."
              />
              <ImageField
                label="Banner"
                kind="banner"
                shape="banner"
                url={draft.banner.url}
                name={draft.name}
                onUploaded={(asset) => update('banner', { id: asset.id, url: asset.url })}
                onRemove={() => update('banner', { id: null, url: null })}
                hint="A wide image, about 1600 × 500 pixels. JPEG, PNG, WebP or GIF, up to 5 MB."
              />
              <fieldset>
                <legend className="mb-2 text-sm font-semibold text-plum">Accent colour</legend>
                <div className="grid grid-cols-2 gap-2 min-[420px]:grid-cols-3">
                  {base.accents.map((accent) => {
                    const checked = draft.accent === accent
                    return (
                      <label
                        key={accent}
                        className={`flex min-h-11 cursor-pointer items-center gap-2.5 rounded-2xl border bg-paper px-3 py-2 has-[:focus-visible]:outline has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-primary ${
                          checked ? 'border-plum ring-1 ring-plum' : 'border-sand-strong hover:border-plum-soft'
                        }`}
                      >
                        <input
                          type="radio"
                          name="accent"
                          value={accent}
                          checked={checked}
                          onChange={() => update('accent', accent)}
                          className="sr-only"
                        />
                        <span
                          aria-hidden
                          className="flex size-7 shrink-0 items-center justify-center rounded-full"
                          style={{ background: ACCENT_SWATCH[accent] }}
                        >
                          {checked ? <Check className={`size-4 ${accent === 'gold' ? 'text-[#1b1f4d]' : 'text-white'}`} strokeWidth={3} /> : null}
                        </span>
                        <span className="text-[0.95rem] font-semibold capitalize text-plum">{ACCENT_LABEL[accent] ?? accent}</span>
                      </label>
                    )
                  })}
                </div>
                <p className="mt-2 text-sm text-plum-soft">Used for buttons and highlights on participant pages.</p>
              </fieldset>
            </div>
          </Panel>
        </div>

        <div className="min-w-0 space-y-5">
          <Panel title="Time and access" description="Participants can sign in and vote only inside this window." actions={phaseChip}>
            <div className="space-y-5">
              <div>
                <InputField
                  label="Programme timezone"
                  value={draft.timezone}
                  list="sv-timezones"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  onChange={(e) => update('timezone', e.target.value)}
                  onBlur={(e) => {
                    const canonical = canonicalTimeZone(e.target.value.trim())
                    if (canonical && canonical !== e.target.value) update('timezone', canonical)
                  }}
                  hint="An IANA name such as Asia/Kolkata or Europe/London. All times in the console use this zone."
                  error={errors.timezone ?? (draft.timezone && !zoneValid ? 'This is not a timezone we recognise. Try Asia/Kolkata.' : null)}
                />
                <datalist id="sv-timezones">
                  {zones.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
              </div>
              {zoneChanged ? (
                <Notice tone="info">
                  The access times below keep their clock times and are read in {zoneLabel(zone)}. Check them before saving.
                </Notice>
              ) : null}
              <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                <ZonedDateTimeField
                  label="Access opens"
                  value={draft.opens}
                  onChange={(value) => update('opens', value)}
                  timeZone={zone}
                  error={errors.opens}
                />
                <ZonedDateTimeField
                  label="Access closes"
                  value={draft.closes}
                  onChange={(value) => update('closes', value)}
                  timeZone={zone}
                  error={errors.closes}
                />
              </div>
              <p className="text-sm text-plum-soft">
                Leave both empty to keep access closed. Live voting deadlines cannot be later than the closing time.
              </p>
            </div>
          </Panel>

          <Panel title="How events run">
            <div className="space-y-4">
              <RadioCards<RunMode>
                legend="Run mode"
                name="run_mode"
                value={draft.run_mode}
                onChange={(value) => update('run_mode', value)}
                columns={1}
                options={[
                  {
                    value: 'manual',
                    label: 'Manual',
                    description: 'Events open only when an organiser opens them on Live control.',
                  },
                  {
                    value: 'scheduled',
                    label: 'Scheduled',
                    description: 'Events with planned start and end times open on their own. Any manual change pauses the schedule until you resume it.',
                  },
                ]}
              />
              {draft.run_mode !== base.run_mode ? (
                <Notice tone="info">
                  {draft.run_mode === 'scheduled'
                    ? 'After saving, upcoming events with planned times open automatically. Events without planned times still need an organiser.'
                    : 'After saving, nothing opens automatically. Pending scheduled openings are cancelled.'}
                </Notice>
              ) : base.run_mode === 'scheduled' && base.schedule_suspended ? (
                <Notice tone="warning" title="Manual override active">
                  The schedule is paused
                  {base.schedule_suspended_at ? ` since ${formatWhen(base.schedule_suspended_at, base.timezone)}` : ''}. Resume it on{' '}
                  <Link href="/admin" className="font-semibold text-primary underline underline-offset-2">
                    Live control
                  </Link>
                  .
                </Notice>
              ) : null}
              <div className="divide-y divide-sand border-t border-sand">
                <SwitchField
                  label="Show a countdown"
                  description="Participants see the time left until each voting deadline."
                  checked={draft.countdown_enabled}
                  onChange={(value) => update('countdown_enabled', value)}
                />
                <SwitchField
                  label="Show upcoming event names"
                  description="When off, events that have not opened appear as numbered placeholders."
                  checked={draft.preview_upcoming}
                  onChange={(value) => update('preview_upcoming', value)}
                />
                <SwitchField
                  label="Show planned start times"
                  description="Participants see when each upcoming event is planned to open."
                  checked={draft.publish_schedule}
                  onChange={(value) => update('publish_schedule', value)}
                />
              </div>
              {base.ended_at ? (
                <p className="text-sm text-plum-soft">
                  The programme ended {formatWhen(base.ended_at, base.timezone)}. Resume it on Live control.
                </p>
              ) : null}
            </div>
          </Panel>
        </div>
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
  )
}
