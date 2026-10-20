'use client'

import { FileUp, ListChecks, RotateCcw, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Notice } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'
import { plural } from '@/lib/admin/labels'
import { adminAction } from '@/lib/admin/query'
import type { ImportCommit, ImportPreview, ImportRow, ImportRowStatus } from '@/lib/admin/types'
import { CheckboxField, TextAreaField } from '../form'
import { FilterChips, IconButton, MiniButton, StatusChip, type Tone } from '../primitives'

const STATUS: Record<ImportRowStatus, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'success' },
  invalid: { label: 'Invalid', tone: 'danger' },
  duplicate_in_input: { label: 'Duplicate in list', tone: 'warning' },
  already_imported: { label: 'Already imported', tone: 'neutral' },
}

const ROW_LIMIT = 200

export function ImportPanel({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: (result: ImportCommit, invited: boolean) => void
}) {
  const [source, setSource] = useState<'paste' | 'file'>('paste')
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [previewText, setPreviewText] = useState('')
  const [filter, setFilter] = useState<'all' | ImportRowStatus>('all')
  const [invite, setInvite] = useState(false)
  const [busy, setBusy] = useState<'preview' | 'commit' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const runPreview = async () => {
    if (!text.trim()) {
      setError('Paste at least one email address.')
      return
    }
    setBusy('preview')
    setError(null)
    try {
      const result = await adminAction<ImportPreview>('/admin/participants/import/preview', {
        body: { text },
        timeoutMs: 60_000,
      })
      setPreview(result)
      setPreviewText(text)
      setFilter('all')
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(null)
    }
  }

  const runFilePreview = async (file: File | undefined) => {
    if (!file) return
    setBusy('preview')
    setError(null)
    setFileName(file.name)
    try {
      const form = new FormData()
      form.set('file', file)
      const result = await adminAction<ImportPreview>('/admin/participants/import/preview-file', { form, timeoutMs: 60_000 })
      setPreview(result)
      setPreviewText(result.text ?? '')
      setFilter('all')
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(null)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const commit = async () => {
    setBusy('commit')
    setError(null)
    try {
      const result = await adminAction<ImportCommit>('/admin/participants/import/commit', {
        body: { text: previewText, send_invitations: invite },
        timeoutMs: 120_000,
      })
      onImported(result, invite)
      setPreview(null)
      setText('')
      setPreviewText('')
      setFileName(null)
      setInvite(false)
    } catch (failure) {
      setError(describeError(failure))
    } finally {
      setBusy(null)
    }
  }

  const startOver = () => {
    setPreview(null)
    setPreviewText('')
    setFileName(null)
    setError(null)
  }

  const rows: ImportRow[] = preview ? preview.rows.filter((row) => filter === 'all' || row.status === filter) : []
  const summary = preview?.summary

  return (
    <section aria-labelledby="import-title" className="rounded-[var(--radius-card)] border border-sand bg-paper">
      <div className="flex items-start justify-between gap-3 px-4 pt-4 sm:px-6 sm:pt-5">
        <div className="min-w-0">
          <h2 id="import-title" className="text-[1.05rem] font-bold text-plum">
            Add participants
          </h2>
          <p className="mt-0.5 text-sm text-plum-soft">Check the list first. Nothing is saved until you import.</p>
        </div>
        <IconButton label="Close" onClick={onClose}>
          <X aria-hidden className="size-5" />
        </IconButton>
      </div>

      <div className="space-y-5 px-4 pb-5 pt-4 sm:px-6 sm:pb-6">
        {!preview ? (
          <>
            <div role="group" aria-label="How to add people" className="inline-flex rounded-full border border-sand-strong bg-ivory p-1">
              {(
                [
                  ['paste', 'Paste addresses'],
                  ['file', 'Upload a CSV file'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={source === value}
                  onClick={() => {
                    setSource(value)
                    setError(null)
                  }}
                  className={`min-h-11 rounded-full px-4 text-sm font-semibold transition-colors ${
                    source === value ? 'bg-plum text-paper' : 'text-plum-soft hover:text-plum'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {source === 'paste' ? (
              <div className="space-y-3">
                <TextAreaField
                  label="Email addresses"
                  rows={8}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={'asha@example.com, john@example.com, priya@example.com\nRavi Kumar <ravi@example.com>\nmeera@example.com, Meera Iyer'}
                  hint="Separate addresses with commas or new lines. To add a name, put “Name <email>” or “email, name” on its own line."
                  spellCheck={false}
                  autoCapitalize="none"
                />
                <Button onClick={() => void runPreview()} busy={busy === 'preview'}>
                  <ListChecks aria-hidden className="size-4" />
                  Check addresses
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="rounded-2xl border border-dashed border-sand-strong bg-ivory px-4 py-5">
                  <p className="font-semibold text-plum">CSV file</p>
                  <p id="csv-hint" className="mt-0.5 text-sm text-plum-soft">
                    UTF-8, up to 2 MB, with a header row. It needs an <strong>email</strong> column; a <strong>name</strong> or{' '}
                    <strong>display_name</strong> column is optional.
                  </p>
                  <MiniButton
                    className="mt-3"
                    busy={busy === 'preview'}
                    aria-describedby="csv-hint"
                    onClick={() => fileInput.current?.click()}
                  >
                    <FileUp aria-hidden className="size-4" />
                    Choose a CSV file
                  </MiniButton>
                  {fileName ? <p className="mt-2 text-sm text-plum-soft">Selected: {fileName}</p> : null}
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".csv,text/csv"
                    className="sr-only"
                    tabIndex={-1}
                    aria-hidden
                    onChange={(e) => void runFilePreview(e.target.files?.[0])}
                  />
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            {fileName ? <p className="text-sm text-plum-soft">From {fileName}</p> : null}
            <div aria-live="polite">
              <p className="font-semibold text-plum">
                {summary && summary.new > 0
                  ? `${plural(summary.new, 'new participant')} ready to import.`
                  : 'Nobody new to import.'}
              </p>
              <dl className="mt-3 flex flex-wrap gap-2">
                {(Object.keys(STATUS) as ImportRowStatus[]).map((status) => (
                  <div key={status} className="contents">
                    <dt className="sr-only">{STATUS[status].label}</dt>
                    <dd>
                      <StatusChip tone={STATUS[status].tone} label={`${STATUS[status].label}: ${summary?.[status] ?? 0}`} />
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <FilterChips<'all' | ImportRowStatus>
              label="Show rows"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: 'All rows', count: preview.rows.length },
                ...(Object.keys(STATUS) as ImportRowStatus[])
                  .filter((status) => (summary?.[status] ?? 0) > 0)
                  .map((status) => ({ value: status, label: STATUS[status].label, count: summary?.[status] ?? 0 })),
              ]}
            />

            <ul className="divide-y divide-sand rounded-2xl border border-sand md:hidden">
              {rows.slice(0, ROW_LIMIT).map((row, index) => (
                <li key={`${row.row}-${index}`} className="px-3 py-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="min-w-0 break-all font-semibold text-plum">{row.email ?? row.input}</p>
                    <StatusChip tone={STATUS[row.status].tone} label={STATUS[row.status].label} />
                  </div>
                  <p className="mt-0.5 text-plum-soft">
                    Line {row.row}
                    {row.display_name ? `, ${row.display_name}` : ''}
                  </p>
                  {row.reason ? <p className="mt-0.5 break-words text-plum-soft">{row.reason}</p> : null}
                </li>
              ))}
            </ul>
            <div className="hidden overflow-x-auto rounded-2xl border border-sand md:block">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Import check, one row per line of input</caption>
                <thead className="bg-ivory text-plum-soft">
                  <tr>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Line
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Email
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Name
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Result
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-sand">
                  {rows.slice(0, ROW_LIMIT).map((row, index) => (
                    <tr key={`${row.row}-${index}`} className="align-top">
                      <td className="tabular px-3 py-2.5 text-plum-soft">{row.row}</td>
                      <th scope="row" className="max-w-[16rem] break-words px-3 py-2.5 font-medium text-plum">
                        {row.email ?? row.input}
                      </th>
                      <td className="max-w-[12rem] break-words px-3 py-2.5 text-plum-soft">{row.display_name || '—'}</td>
                      <td className="px-3 py-2.5">
                        <StatusChip tone={STATUS[row.status].tone} label={STATUS[row.status].label} />
                        {row.reason ? <p className="mt-1 max-w-[18rem] break-words text-plum-soft">{row.reason}</p> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length > ROW_LIMIT ? (
              <p className="text-sm text-plum-soft">
                Showing the first {ROW_LIMIT} of {rows.length.toLocaleString('en-IN')} rows. Every row is checked again when you import.
              </p>
            ) : null}

            {summary && summary.new > 0 ? (
              <CheckboxField
                label="Send invitations now"
                description="Each new participant gets an email with their user ID (their email address) and a password, and can sign in straight away. You can also invite them later."
                checked={invite}
                onChange={setInvite}
              />
            ) : null}
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <Button onClick={() => void commit()} busy={busy === 'commit'} disabled={!summary || summary.new === 0}>
                <Upload aria-hidden className="size-4" />
                {summary && summary.new > 0 ? `Import ${plural(summary.new, 'participant')}` : 'Nothing to import'}
              </Button>
              <Button variant="secondary" onClick={startOver} disabled={busy !== null}>
                <RotateCcw aria-hidden className="size-4" />
                Start over
              </Button>
            </div>
          </>
        )}
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
      </div>
    </section>
  )
}
