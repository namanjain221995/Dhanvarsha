'use client'

import { ArrowDown, ArrowUp, ListChecks, Lock, MessageSquareText, Pencil, Plus, Trash2, UserRound, UsersRound } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Avatar } from '@/components/ui/misc'
import { describeError, errorCode, fieldErrors } from '@/lib/admin/errors'
import { BALLOT_KIND_OPTIONS, ballotKindLabel, isQuestion, plural } from '@/lib/admin/labels'
import { adminAction } from '@/lib/admin/query'
import { isSmallCorrection } from '@/lib/admin/similar'
import type { AdminBallot, AdminEvent, AdminNominee, BallotKind } from '@/lib/admin/types'
import { formatMembers } from '@/lib/members'
import { ImageField, InputField, SelectField, TextAreaField } from '../form'
import { focusSoon, IconButton, MiniButton, StatusChip } from '../primitives'

export type BallotActions = {
  /** Refetch the event after a change. */
  refresh: () => Promise<void>
  announce: (message: string) => void
  askDeleteBallot: (ballot: AdminBallot, from: Element) => void
  askDeleteNominee: (nominee: AdminNominee, ballot: AdminBallot, from: Element) => void
}

/** Ballot titles: a question for mcq and text ballots, so longer than a name. */
const TITLE_MAX = 200
/** Typed answers: what participants may type (the API checks the same). */
const ANSWER_MAX = 500

function KindIcon({ kind }: { kind: BallotKind }) {
  if (kind === 'team') return <UsersRound aria-hidden className="size-3.5" />
  if (kind === 'individual') return <UserRound aria-hidden className="size-3.5" />
  if (kind === 'mcq') return <ListChecks aria-hidden className="size-3.5" />
  return <MessageSquareText aria-hidden className="size-3.5" />
}

/** A, B, C, ... as participants see the answer options. */
function optionLetter(index: number): string {
  return index < 26 ? String.fromCharCode(65 + index) : String(index + 1)
}

/** Maps refusals about a ballot's type to the type field. */
function kindError(failure: unknown): string | null {
  const code = errorCode(failure)
  return code === 'TEXT_QUESTION_HAS_NO_OPTIONS' || code === 'OPTION_HAS_NO_PHOTO' ? describeError(failure) : null
}

export function BallotCard({
  event,
  ballot,
  index,
  actions,
}: {
  event: AdminEvent
  ballot: AdminBallot
  index: number
  actions: BallotActions
}) {
  const [editing, setEditing] = useState(false)
  const [adding, setAdding] = useState(false)
  const [editingNominee, setEditingNominee] = useState<string | null>(null)
  const [moving, setMoving] = useState(false)
  const [moveError, setMoveError] = useState<string | null>(null)
  const structural = !ballot.frozen && event.status === 'upcoming'
  const headingId = `ballot-${ballot.id}`
  const question = isQuestion(ballot.kind)
  const mcq = ballot.kind === 'mcq'
  const noun = mcq ? 'answer option' : 'nominee'

  const move = async (nominee: AdminNominee, direction: -1 | 1) => {
    const order = ballot.nominees.map((n) => n.id)
    const index = order.indexOf(nominee.id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= order.length) return
    order.splice(index, 1)
    order.splice(target, 0, nominee.id)
    setMoving(true)
    setMoveError(null)
    try {
      await adminAction(`/admin/ballots/${ballot.id}/nominees/reorder`, { body: { ordered_ids: order } })
      await actions.refresh()
      actions.announce(`${nominee.name} is now number ${target + 1} on ${ballot.title}.`)
      window.requestAnimationFrame(() => {
        const preferred = document.getElementById(`nominee-${direction < 0 ? 'up' : 'down'}-${nominee.id}`) as HTMLButtonElement | null
        const fallback = document.getElementById(`nominee-${direction < 0 ? 'down' : 'up'}-${nominee.id}`) as HTMLButtonElement | null
        ;(preferred && !preferred.disabled ? preferred : fallback)?.focus()
      })
    } catch (error) {
      setMoveError(describeError(error))
    } finally {
      setMoving(false)
    }
  }

  return (
    <section aria-labelledby={headingId} className="min-w-0 rounded-[var(--radius-card)] border border-sand bg-paper">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-6 sm:pt-5">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-plum-soft">
            {question ? 'Question' : 'Ballot'} {index + 1}
          </p>
          <h3 id={headingId} className="break-words text-[1.15rem] font-bold text-plum">
            {ballot.title}
          </h3>
          <div className="mt-1.5 flex flex-wrap gap-2">
            <StatusChip tone="info" icon={<KindIcon kind={ballot.kind} />} label={ballotKindLabel(ballot.kind)} />
            {ballot.frozen ? <StatusChip tone="neutral" icon={<Lock aria-hidden className="size-3.5" />} label="Locked" /> : null}
            {ballot.vote_count ? (
              <StatusChip tone="info" label={plural(ballot.vote_count, question ? 'answer' : 'vote')} />
            ) : null}
          </div>
          {ballot.description ? <p className="mt-2 max-w-2xl text-sm text-plum-soft">{ballot.description}</p> : null}
        </div>
        <div className="flex flex-wrap gap-1">
          {!editing ? (
            <MiniButton onClick={() => setEditing(true)}>
              <Pencil aria-hidden className="size-4" />
              {question ? 'Edit question' : 'Edit ballot'}
              <span className="sr-only">: {ballot.title}</span>
            </MiniButton>
          ) : null}
          {structural ? (
            <MiniButton
              variant="danger"
              aria-label={`Remove ${question ? 'question' : 'ballot'} ${ballot.title}…`}
              onClick={(e) => actions.askDeleteBallot(ballot, e.currentTarget)}
            >
              <Trash2 aria-hidden className="size-4" />
              Remove…
            </MiniButton>
          ) : null}
        </div>
      </div>

      {editing ? (
        <div className="px-4 pt-4 sm:px-6">
          <BallotForm
            ballot={ballot}
            onCancel={() => setEditing(false)}
            onSaved={async (message) => {
              await actions.refresh()
              setEditing(false)
              actions.announce(message)
            }}
          />
        </div>
      ) : null}

      {ballot.kind === 'text' ? (
        <div className="px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
          <p className="flex items-start gap-2 rounded-2xl bg-ivory px-4 py-3 text-sm text-plum">
            <MessageSquareText aria-hidden className="mt-0.5 size-4 shrink-0 text-plum-soft" />
            Voters type their answer (up to {ANSWER_MAX} characters).
          </p>
        </div>
      ) : (
        <div className="px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
          <h4 className="text-sm font-semibold text-plum">
            {mcq ? 'Answer options' : 'Nominees'} <span className="font-normal text-plum-soft">({ballot.nominees.length})</span>
          </h4>
          {moveError ? (
            <p role="alert" className="mt-2 text-sm font-medium text-danger">
              {moveError}
            </p>
          ) : null}
          {ballot.nominees.length === 0 || (mcq && ballot.nominees.length < 2 && event.status === 'upcoming') ? (
            <p className="mt-2 rounded-2xl border border-dashed border-gold/60 bg-gold-wash/50 px-4 py-3 text-sm text-plum">
              {mcq
                ? ballot.nominees.length === 0
                  ? 'No answer options yet. The event cannot open until this question has at least two.'
                  : 'Add at least one more answer option. The event cannot open until this question has two.'
                : 'No nominees yet. The event cannot open until every ballot has at least one.'}
            </p>
          ) : null}
          {ballot.nominees.length === 0 ? null : (
            <ol className="mt-2 divide-y divide-sand">
              {ballot.nominees.map((nominee, position) => (
                <li key={nominee.id} className="py-3">
                  {editingNominee === nominee.id ? (
                    <NomineeForm
                      ballot={ballot}
                      nominee={nominee}
                      onCancel={() => setEditingNominee(null)}
                      onSaved={async (message) => {
                        await actions.refresh()
                        setEditingNominee(null)
                        actions.announce(message)
                      }}
                    />
                  ) : (
                    <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
                      {mcq ? (
                        <span
                          aria-hidden
                          className="type-condensed flex size-12 items-center justify-center rounded-[14px] border-2 border-sand-strong bg-ivory text-[1.15rem] font-bold text-plum"
                        >
                          {optionLetter(position)}
                        </span>
                      ) : (
                        <Avatar src={nominee.photo_url} name={nominee.name} size={48} />
                      )}
                      <div className="min-w-0">
                        <p className="break-words font-semibold text-plum">
                          {mcq ? <span className="sr-only">{optionLetter(position)}. </span> : null}
                          {nominee.name}
                        </p>
                        {nominee.affiliation && !mcq ? <p className="break-words text-sm text-plum-soft">{nominee.affiliation}</p> : null}
                        {ballot.kind === 'team' && nominee.members.length ? (
                          <p className="flex items-start gap-1.5 break-words text-sm text-plum">
                            <UsersRound aria-hidden className="mt-[0.2rem] size-3.5 shrink-0 text-plum-soft" />
                            <span>
                              <span className="sr-only">Team members: </span>
                              {formatMembers(nominee.members)}
                            </span>
                          </p>
                        ) : null}
                        {nominee.description ? (
                          <p className="mt-0.5 line-clamp-2 break-words text-sm text-plum-soft">{nominee.description}</p>
                        ) : null}
                      </div>
                      <div className="col-start-2 -ml-3 flex flex-wrap gap-0.5 sm:col-start-3 sm:ml-0 sm:justify-end">
                        {structural ? (
                          <>
                            <IconButton
                              id={`nominee-up-${nominee.id}`}
                              label={`Move ${nominee.name} up`}
                              disabled={position === 0 || moving}
                              onClick={() => void move(nominee, -1)}
                            >
                              <ArrowUp aria-hidden className="size-[1.1rem]" />
                            </IconButton>
                            <IconButton
                              id={`nominee-down-${nominee.id}`}
                              label={`Move ${nominee.name} down`}
                              disabled={position === ballot.nominees.length - 1 || moving}
                              onClick={() => void move(nominee, 1)}
                            >
                              <ArrowDown aria-hidden className="size-[1.1rem]" />
                            </IconButton>
                          </>
                        ) : null}
                        <IconButton label={`Edit ${nominee.name}`} onClick={() => setEditingNominee(nominee.id)}>
                          <Pencil aria-hidden className="size-[1.1rem]" />
                        </IconButton>
                        {structural ? (
                          <IconButton
                            label={`Remove ${nominee.name}`}
                            className="hover:bg-danger-wash hover:text-danger"
                            onClick={(e) => actions.askDeleteNominee(nominee, ballot, e.currentTarget)}
                          >
                            <Trash2 aria-hidden className="size-[1.1rem]" />
                          </IconButton>
                        ) : null}
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}

          {structural ? (
            adding ? (
              <div className="mt-3 rounded-2xl border border-sand bg-ivory p-4">
                <NomineeForm
                  ballot={ballot}
                  onCancel={() => setAdding(false)}
                  onSaved={async (message) => {
                    await actions.refresh()
                    actions.announce(message)
                  }}
                />
              </div>
            ) : (
              <MiniButton className="mt-3" onClick={() => setAdding(true)}>
                <Plus aria-hidden className="size-4" />
                Add {noun}
                <span className="sr-only"> to {ballot.title}</span>
              </MiniButton>
            )
          ) : null}
        </div>
      )}
    </section>
  )
}

function BallotForm({
  ballot,
  onCancel,
  onSaved,
}: {
  ballot: AdminBallot
  onCancel: () => void
  onSaved: (message: string) => Promise<void>
}) {
  const [title, setTitle] = useState(ballot.title)
  const [kind, setKind] = useState<BallotKind>(ballot.kind)
  const [description, setDescription] = useState(ballot.description)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const titleRef = useRef<HTMLInputElement>(null)
  useEffect(() => focusSoon(titleRef.current), [])
  const titleChanged = title.trim() !== ballot.title
  const needsReason = ballot.frozen && titleChanged
  const tooDifferent = needsReason && title.trim() !== '' && !isSmallCorrection(ballot.title, title)
  const question = isQuestion(kind)
  const hasOptions = ballot.nominees.length > 0

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const local: Record<string, string> = {}
    if (!title.trim()) local.title = question ? 'Type the question.' : 'Give the ballot a title.'
    if (kind === 'text' && kind !== ballot.kind && hasOptions) {
      local.kind = `Remove the ${ballot.kind === 'mcq' ? 'answer options' : 'nominees'} first: a typed-answer question has none.`
    }
    if (needsReason && !reason.trim()) local.reason = 'Say why the title is being corrected. It goes in the audit log.'
    setErrors(local)
    if (Object.keys(local).length) return
    const body: Record<string, unknown> = {}
    if (titleChanged) body.title = title.trim()
    if (kind !== ballot.kind) body.kind = kind
    if (description.trim() !== ballot.description) body.description = description.trim()
    if (needsReason) body.reason = reason.trim()
    if (!Object.keys(body).length) {
      onCancel()
      return
    }
    setBusy(true)
    setError(null)
    try {
      await adminAction(`/admin/ballots/${ballot.id}`, { method: 'PATCH', body })
      await onSaved(`${question ? 'Question' : 'Ballot'} “${title.trim()}” saved.`)
    } catch (failure) {
      const fields = fieldErrors(failure)
      const wrongKind = kindError(failure)
      if (wrongKind) fields.kind = wrongKind
      else if (errorCode(failure) === 'BALLOT_FROZEN') fields.title = describeError(failure)
      else if (/reason/i.test(describeError(failure))) fields.reason = describeError(failure)
      if (Object.keys(fields).length) setErrors(fields)
      else setError(describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4 rounded-2xl border border-sand bg-ivory p-4">
      <InputField
        ref={titleRef}
        label={question ? 'Question' : 'Ballot title'}
        value={title}
        maxLength={TITLE_MAX}
        onChange={(e) => setTitle(e.target.value)}
        error={errors.title}
        hint={
          ballot.frozen
            ? tooDifferent
              ? 'This looks like more than a small correction and will probably be refused. A different question needs a new event.'
              : 'Voting has opened, so only small spelling corrections are allowed.'
            : undefined
        }
      />
      {needsReason ? (
        <InputField
          label="Reason for the correction"
          value={reason}
          maxLength={300}
          onChange={(e) => setReason(e.target.value)}
          error={errors.reason}
          hint="Recorded in the audit log with the old and new title."
        />
      ) : null}
      <SelectField
        label="Ballot type"
        value={kind}
        onChange={(e) => setKind(e.target.value as BallotKind)}
        disabled={ballot.frozen}
        error={errors.kind}
        hint={
          ballot.frozen
            ? 'Locked because voting has opened.'
            : hasOptions && ballot.kind !== 'text'
              ? `A typed-answer question has no ${ballot.kind === 'mcq' ? 'answer options' : 'nominees'}: remove them first to make it one.`
              : undefined
        }
      >
        {BALLOT_KIND_OPTIONS.map((option) => (
          <option key={option.value} value={option.value} disabled={option.value === 'text' && hasOptions && ballot.kind !== 'text'}>
            {option.label}
          </option>
        ))}
      </SelectField>
      <TextAreaField
        label="Description"
        optional
        rows={2}
        maxLength={2000}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        error={errors.description}
      />
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" busy={busy}>
          {question ? 'Save question' : 'Save ballot'}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

const MAX_MEMBERS = 30

/** "Asha Rao, Ravi Kumar" or one name per line: trimmed, blanks dropped, repeats kept once. */
function parseMembers(text: string): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of text.split(/[\n,]/)) {
    const name = raw.trim().replace(/\s+/g, ' ')
    if (name && !seen.has(name.toLowerCase())) {
      seen.add(name.toLowerCase())
      out.push(name)
    }
  }
  return out
}

export function NomineeForm({
  ballot,
  nominee,
  onCancel,
  onSaved,
}: {
  ballot: AdminBallot
  nominee?: AdminNominee
  onCancel: () => void
  onSaved: (message: string) => Promise<void>
}) {
  const [name, setName] = useState(nominee?.name ?? '')
  const [affiliation, setAffiliation] = useState(nominee?.affiliation ?? '')
  const [description, setDescription] = useState(nominee?.description ?? '')
  const [membersText, setMembersText] = useState((nominee?.members ?? []).join('\n'))
  const [photo, setPhoto] = useState<{ id: string | null; url: string | null }>({
    id: nominee?.photo_asset_id ?? null,
    url: nominee?.photo_url ?? null,
  })
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => focusSoon(nameRef.current), [])
  const frozen = ballot.frozen
  const nameChanged = nominee ? name.trim() !== nominee.name : true
  const needsReason = Boolean(nominee) && frozen && nameChanged
  const tooDifferent = needsReason && name.trim() !== '' && nominee !== undefined && !isSmallCorrection(nominee.name, name)

  const isTeam = ballot.kind === 'team'
  // Answer options of a multiple-choice question: a name and an optional description only.
  const isOption = ballot.kind === 'mcq'
  const members = isTeam ? parseMembers(membersText) : (nominee?.members ?? [])
  const membersChanged = nominee ? members.join('\n') !== nominee.members.join('\n') : members.length > 0

  const reset = () => {
    setName('')
    setAffiliation('')
    setDescription('')
    setMembersText('')
    setPhoto({ id: null, url: null })
    window.requestAnimationFrame(() => nameRef.current?.focus())
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const local: Record<string, string> = {}
    if (!name.trim()) local.name = isOption ? 'Type the answer option.' : 'Enter the nominee’s name.'
    if (needsReason && !reason.trim()) local.reason = 'Say why the name is being corrected. It goes in the audit log.'
    setErrors(local)
    if (Object.keys(local).length) return
    setBusy(true)
    setError(null)
    try {
      if (!nominee) {
        await adminAction(`/admin/ballots/${ballot.id}/nominees`, {
          body: isOption
            ? { name: name.trim(), description: description.trim() }
            : {
                name: name.trim(),
                affiliation: affiliation.trim(),
                description: description.trim(),
                ...(members.length ? { members } : {}),
                ...(photo.id ? { photo_asset_id: photo.id } : {}),
              },
        })
        await onSaved(`${name.trim()} added to ${ballot.title}.`)
        reset()
      } else {
        const body: Record<string, unknown> = {}
        if (nameChanged) body.name = name.trim()
        if (!isOption && affiliation.trim() !== nominee.affiliation) body.affiliation = affiliation.trim()
        if (description.trim() !== nominee.description) body.description = description.trim()
        if (membersChanged && !isOption) body.members = members
        if (!isOption && photo.id !== nominee.photo_asset_id) {
          if (photo.id) body.photo_asset_id = photo.id
          else body.clear_photo = true
        }
        if (needsReason) body.reason = reason.trim()
        if (!Object.keys(body).length) {
          onCancel()
          return
        }
        await adminAction(`/admin/nominees/${nominee.id}`, { method: 'PATCH', body })
        await onSaved(`${name.trim()} saved.`)
      }
    } catch (failure) {
      const fields = fieldErrors(failure)
      // Errors on one member name arrive as "members.<index>".
      for (const key of Object.keys(fields)) {
        if (/^\d+$/.test(key)) {
          fields.members = `Member ${Number(key) + 1}: ${fields[key]}`
          delete fields[key]
        }
      }
      const message = describeError(failure)
      if (errorCode(failure) === 'NOMINEE_FROZEN') fields.name = message
      else if (/reason/i.test(message)) fields.reason = message
      if (Object.keys(fields).length) setErrors(fields)
      else setError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4">
      <p className="font-semibold text-plum">
        {nominee ? `Edit ${nominee.name}` : isOption ? `New answer option on ${ballot.title}` : `New nominee on ${ballot.title}`}
      </p>
      <div className={`grid gap-4 ${isOption ? '' : 'sm:grid-cols-2'}`}>
        <InputField
          ref={nameRef}
          label={isOption ? 'Answer option' : ballot.kind === 'team' ? 'Team name' : 'Name'}
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          error={errors.name}
          hint={
            nominee && frozen
              ? tooDifferent
                ? 'This looks like more than a spelling correction and will probably be refused.'
                : 'Voting has opened, so only small spelling corrections are allowed.'
              : undefined
          }
        />
        {isOption ? null : (
          <InputField
            label={ballot.kind === 'team' ? 'Organisation or school' : 'Team or organisation'}
            optional
            value={affiliation}
            maxLength={120}
            onChange={(e) => setAffiliation(e.target.value)}
            error={errors.affiliation}
          />
        )}
      </div>
      {needsReason ? (
        <InputField
          label="Reason for the correction"
          value={reason}
          maxLength={300}
          onChange={(e) => setReason(e.target.value)}
          error={errors.reason}
          hint="Recorded in the audit log with the old and new name."
        />
      ) : null}
      {isTeam ? (
        <TextAreaField
          label="Team members"
          optional
          rows={4}
          value={membersText}
          onChange={(e) => setMembersText(e.target.value)}
          error={errors.members}
          placeholder={'Asha Rao\nRavi Kumar\nMeera Pillai'}
          hint={`One name per line, or separated by commas (up to ${MAX_MEMBERS}). Voters see them under the team name; the vote goes to the whole team.${
            nominee && frozen ? ' You can still correct this list after voting opens.' : ''
          }`}
        />
      ) : null}
      <TextAreaField
        label="Short description"
        optional
        rows={2}
        maxLength={1000}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        error={errors.description}
      />
      {isOption ? null : (
        <ImageField
          label="Photo"
          kind="nominee_photo"
          shape="round"
          url={photo.url}
          name={name || nominee?.name || '?'}
          onUploaded={(asset) => setPhoto({ id: asset.id, url: asset.url })}
          onRemove={() => setPhoto({ id: null, url: null })}
          hint="Without a photo, participants see the initials. JPEG, PNG, WebP or GIF, up to 5 MB."
        />
      )}
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" busy={busy}>
          {isOption ? (nominee ? 'Save option' : 'Add option') : nominee ? 'Save nominee' : 'Add nominee'}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>
          {nominee ? 'Cancel' : 'Done'}
        </Button>
      </div>
    </form>
  )
}

export function AddBallotForm({ event, onSaved, onCancel }: { event: AdminEvent; onSaved: (message: string) => Promise<void>; onCancel: () => void }) {
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<BallotKind>('individual')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const titleRef = useRef<HTMLInputElement>(null)
  useEffect(() => focusSoon(titleRef.current), [])
  const question = isQuestion(kind)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) {
      setErrors({
        title: question
          ? 'Type the question, for example “Which song opened the show?”.'
          : 'Give the ballot a title, for example “Best performer”.',
      })
      return
    }
    setErrors({})
    setBusy(true)
    setError(null)
    try {
      await adminAction(`/admin/events/${event.id}/ballots`, {
        body: { title: title.trim(), kind, description: description.trim() },
      })
      await onSaved(
        kind === 'text'
          ? `Question “${title.trim()}” added. Voters type their answer.`
          : kind === 'mcq'
            ? `Question “${title.trim()}” added. Add its answer options next.`
            : `Ballot “${title.trim()}” added. Add its nominees next.`,
      )
    } catch (failure) {
      const fields = fieldErrors(failure)
      const wrongKind = kindError(failure)
      if (wrongKind) fields.kind = wrongKind
      if (Object.keys(fields).length) setErrors(fields)
      else setError(describeError(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4 rounded-[var(--radius-card)] border border-sand bg-paper p-4 sm:p-6">
      <h3 className="text-[1.05rem] font-bold text-plum">New ballot or question</h3>
      <SelectField label="Ballot type" value={kind} onChange={(e) => setKind(e.target.value as BallotKind)} error={errors.kind}>
        {BALLOT_KIND_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </SelectField>
      <InputField
        ref={titleRef}
        label={question ? 'Question' : 'Ballot title'}
        value={title}
        maxLength={TITLE_MAX}
        onChange={(e) => setTitle(e.target.value)}
        error={errors.title}
        hint={
          kind === 'text'
            ? `Voters type their answer (up to ${ANSWER_MAX} characters). Only organisers see the answers.`
            : kind === 'mcq'
              ? 'Voters pick one of the answer options you add next.'
              : 'The question participants answer, for example “Best performer”.'
        }
      />
      <TextAreaField
        label="Description"
        optional
        rows={2}
        maxLength={2000}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        error={errors.description}
      />
      {error ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" busy={busy}>
          Add ballot
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
