'use client'

import { ExternalLink, LoaderCircle, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Notice } from '@/components/ui/misc'
import { describeError } from '@/lib/admin/errors'
import { plural } from '@/lib/admin/labels'
import { adminAction, useAdminQuery } from '@/lib/admin/query'
import type { GalleryAnalysis, GalleryDescription, GalleryFaceAnalysis } from '@/lib/admin/types'
import { formatDuration } from '@/lib/gallery'
import { LoadError, MiniButton, SkeletonBlock } from '../primitives'

/**
 * What OpenCV and the vision model made of one file. Only organisers ever see this: a box on each
 * face, who it was matched with and how, and the model's description. For a video, the boxes sit on
 * the exact frames OpenCV read. Visitors see the file with no marks.
 */

const MODEL_NAME = 'Qwen'

type Tone = 'clear' | 'model' | 'new'

function toneOf(face: GalleryFaceAnalysis): Tone {
  if (face.matched_by === 'model') return 'model'
  return face.matched_by === 'new' ? 'new' : 'clear'
}

const BOX: Record<Tone, string> = { clear: 'border-[#f2c14e]', model: 'border-[#38bdf8]', new: 'border-white' }
const TAG: Record<Tone, string> = {
  clear: 'bg-[#f2c14e] text-[#1b1f4d]',
  model: 'bg-[#38bdf8] text-[#06283d]',
  new: 'bg-white text-[#1b1f4d]',
}

const score = (value: number) => value.toFixed(2)
const personName = (face: GalleryFaceAnalysis) => face.name || 'Untitled'

/** In words: how this face came to belong to this person. */
function decision(face: GalleryFaceAnalysis): string {
  const close = face.checked?.find((entry) => entry.model !== 'not_asked')
  const whom = close ? close.name || (close.listed ? 'an unnamed person' : 'someone no longer listed') : ''
  switch (face.matched_by) {
    case 'opencv':
      return face.match_score !== null ? `OpenCV ${score(face.match_score)}: clear match.` : 'OpenCV: clear match.'
    case 'model':
      return face.match_score !== null
        ? `OpenCV ${score(face.match_score)}: close, so ${MODEL_NAME} was asked. It said same person.`
        : `${MODEL_NAME} said same person.`
    case 'merge':
      return 'An organiser merged this person.'
    default:
      if (close?.model === 'no') return `OpenCV ${score(close.score)} with ${whom}: close. ${MODEL_NAME} said different person, so this is someone new.`
      if (close?.model === 'no_answer') return `OpenCV ${score(close.score)} with ${whom}: close. ${MODEL_NAME} did not answer, so this counts as someone new.`
      return face.matched_by === 'new' && face.has_selfie ? 'Joined later, when this person added a selfie.' : 'No match found: someone new.'
  }
}

function PictureWithBoxes({
  src,
  alt,
  ratio,
  faces,
  showBoxes,
  selected,
  onSelect,
}: {
  src: string
  alt: string
  ratio: number
  faces: GalleryFaceAnalysis[]
  showBoxes: boolean
  selected: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div className="relative mx-auto bg-black" style={{ aspectRatio: ratio, width: `min(100%, calc(68dvh * ${ratio}))` }}>
      {/* eslint-disable-next-line @next/next/no-img-element -- the file or frame, from the API */}
      <img src={src} alt={alt} className="block size-full object-contain" />
      {showBoxes
        ? faces.map((face) => {
            if (!face.box) return null
            const [left, top, width, height] = face.box
            const tone = toneOf(face)
            const active = selected === face.id
            return (
              <button
                key={face.id}
                type="button"
                data-analysis-box=""
                aria-label={`Face ${face.number}: ${personName(face)}`}
                aria-pressed={active}
                onClick={() => onSelect(face.id)}
                className={`absolute rounded-[3px] border-2 ${BOX[tone]} ${active ? 'bg-white/20 shadow-[0_0_0_2px_rgb(0_0_0/0.6)]' : 'hover:bg-white/10'}`}
                style={{ left: `${left * 100}%`, top: `${top * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}
              >
                <span className={`absolute -top-[1.05rem] left-[-2px] rounded-sm px-1 text-[0.68rem] font-bold leading-4 ${TAG[tone]}`}>{face.number}</span>
              </button>
            )
          })
        : null}
    </div>
  )
}

function Description({ data, onDescribed }: { data: GalleryAnalysis; onDescribed: (answer: GalleryDescription) => void }) {
  const [problem, setProblem] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const mediaId = data.media.id
  const wanted = data.model_enabled && !data.description && data.media.status === 'ready'

  useEffect(() => {
    if (!wanted) return
    const controller = new AbortController()
    adminAction<GalleryDescription>(`/admin/gallery/media/${mediaId}/analysis/describe`, { signal: controller.signal, timeoutMs: 120_000 })
      .then((answer) => onDescribed(answer))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setProblem(describeError(error))
      })
    return () => controller.abort()
    // onDescribed only updates the cached answer; asking again is for `attempt`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, mediaId, attempt])

  if (!data.model_enabled) return <p className="text-sm text-plum-soft">No vision model is set, so nothing describes this file.</p>
  if (data.description) {
    const faces = data.faces.length
    return (
      <>
        <p className="text-sm text-plum">{data.description.summary}</p>
        <p className="mt-1 text-sm text-plum-soft">
          {MODEL_NAME} counts {plural(data.description.people, 'person', 'people')}.{' '}
          OpenCV {data.media.type === 'video' ? 'kept' : 'found'} {plural(faces, 'face')}.
        </p>
      </>
    )
  }
  if (problem) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-plum-soft">
        <span>{problem}</span>
        <MiniButton
          onClick={() => {
            setProblem(null)
            setAttempt((n) => n + 1)
          }}
        >
          Try again
        </MiniButton>
      </div>
    )
  }
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-plum-soft">
      <LoaderCircle aria-hidden className="size-4 animate-spin" />
      Asking {MODEL_NAME} what this shows
    </p>
  )
}

export function AnalysisDialog({ mediaId, name, onClose }: { mediaId: string; name: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const query = useAdminQuery<GalleryAnalysis>(`/admin/gallery/media/${mediaId}/analysis`)
  const [selected, setSelected] = useState<string | null>(null)
  const [chosenFrame, setChosenFrame] = useState<number | null>(null)
  const [showBoxes, setShowBoxes] = useState(true)
  const data = query.data

  useEffect(() => {
    const element = dialog.current
    if (element && !element.open) element.showModal()
    return () => {
      if (element?.open) element.close()
    }
  }, [])

  // A file the worker has not read again yet fills in by itself.
  const reloadSoon = query.reloadSoon
  const unread = data ? !data.read : false
  useEffect(() => {
    if (!unread) return
    const timer = window.setInterval(() => reloadSoon(1000), 4000)
    return () => window.clearInterval(timer)
  }, [unread, reloadSoon])

  const frames = data?.frames ?? []
  // A video opens on the frame with the most faces in it.
  const facesIn = (ms: number) => data?.faces.filter((face) => face.frame_ms === ms).length ?? 0
  const fullestFrame = frames.reduce<(typeof frames)[number] | undefined>((best, entry) => (!best || facesIn(entry.ms) > facesIn(best.ms) ? entry : best), undefined)
  const frame = frames.find((entry) => entry.ms === chosenFrame) ?? fullestFrame
  const isVideo = data?.media.type === 'video'
  const facesHere = isVideo ? (data?.faces ?? []).filter((face) => face.frame_ms === frame?.ms) : (data?.faces ?? [])
  const ratio = data && data.media.width > 0 && data.media.height > 0 ? data.media.width / data.media.height : 4 / 3
  const picture = isVideo ? (frame?.url ?? data?.media.thumb_url) : data?.media.url

  const choose = (face: GalleryFaceAnalysis) => {
    setSelected(face.id)
    if (face.frame_ms !== null) setChosenFrame(face.frame_ms)
  }

  const counts = (data?.faces ?? []).reduce(
    (total, face) => {
      total[toneOf(face)] += 1
      return total
    },
    { clear: 0, model: 0, new: 0 } as Record<Tone, number>,
  )

  return (
    <dialog
      ref={dialog}
      aria-label={`Analysis of ${name}`}
      className="m-auto max-h-[calc(100dvh-1.5rem)] w-[calc(100%-1.5rem)] max-w-6xl overflow-y-auto rounded-[1.5rem] border border-sand bg-paper p-0 text-plum shadow-[var(--shadow-lift)] backdrop:bg-plum/50"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-bold leading-snug">What the analysis saw</h2>
            <p className="truncate text-sm font-semibold text-plum-soft" title={name}>
              {name || 'Untitled file'}
            </p>
            <p className="text-sm text-plum-soft">Only organisers see this. Visitors see the file with no marks.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="-mr-2 -mt-1 flex size-11 shrink-0 items-center justify-center rounded-full text-plum-soft hover:bg-plum/5 hover:text-plum">
            <X aria-hidden className="size-5" />
          </button>
        </div>

        {query.error && !data ? <LoadError error={query.error} onRetry={() => void query.reload()} what="the analysis" /> : null}
        {!data && !query.error ? <SkeletonBlock className="mt-4 h-96 w-full" /> : null}

        {data ? (
          <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
            <div className="min-w-0">
              {picture ? (
                <PictureWithBoxes
                  src={picture}
                  alt={isVideo ? `A frame of ${name}` : name}
                  ratio={ratio}
                  faces={facesHere}
                  showBoxes={showBoxes}
                  selected={selected}
                  onSelect={(id) => setSelected(id)}
                />
              ) : (
                <SkeletonBlock className="h-64 w-full" />
              )}
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-plum-soft">
                <label className="inline-flex min-h-8 items-center gap-2">
                  <input type="checkbox" checked={showBoxes} onChange={(event) => setShowBoxes(event.target.checked)} className="size-4 accent-[var(--primary)]" />
                  Show boxes
                </label>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="size-3 rounded-[2px] border-2 border-[#f2c14e] bg-plum" /> Clear match
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="size-3 rounded-[2px] border-2 border-[#38bdf8] bg-plum" /> {MODEL_NAME} decided
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="size-3 rounded-[2px] border-2 border-white bg-plum outline outline-1 outline-plum-faint" /> Someone new
                </span>
              </div>
              {isVideo && frames.length > 0 ? (
                <div className="mt-3">
                  <p className="text-sm font-semibold">Frames</p>
                  <ul className="mt-1 flex gap-2 overflow-x-auto pb-1">
                    {frames.map((entry) => {
                      const count = data.faces.filter((face) => face.frame_ms === entry.ms).length
                      return (
                        <li key={entry.ms} className="shrink-0">
                          <button
                            type="button"
                            aria-pressed={entry.ms === frame?.ms}
                            aria-label={`Frame at ${formatDuration(entry.ms)}, ${plural(count, 'face')}`}
                            onClick={() => setChosenFrame(entry.ms)}
                            className={`relative block h-16 overflow-hidden rounded-lg border-2 ${entry.ms === frame?.ms ? 'border-primary' : 'border-transparent'}`}
                          >
                            {/* eslint-disable-next-line @next/next/no-img-element -- a frame from the API */}
                            <img src={entry.url} alt="" loading="lazy" className="block h-full w-auto" />
                            <span className="absolute bottom-0.5 left-0.5 rounded bg-plum/80 px-1 text-[0.68rem] font-semibold text-white">{formatDuration(entry.ms)}</span>
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                  {data.media.url ? (
                    <a href={data.media.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex min-h-8 items-center gap-1.5 text-sm font-semibold text-primary underline">
                      Play the video
                      <ExternalLink aria-hidden className="size-3.5" />
                    </a>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="min-w-0 space-y-4">
              {!data.read ? (
                <Notice tone="info">This file was shared before this view existed. It is being read again now, and the boxes appear here by themselves.</Notice>
              ) : null}
              <section aria-label="OpenCV">
                <h3 className="text-sm font-bold">OpenCV</h3>
                {isVideo ? (
                  <p className="mt-0.5 text-sm text-plum">
                    Read {plural(data.frames_read ?? 0, 'frame')} and saw faces {(data.faces_seen ?? 0).toLocaleString('en-IN')} times, which came to {plural(data.faces.length, 'different person', 'different people')}.
                  </p>
                ) : (
                  <p className="mt-0.5 text-sm text-plum">Found {plural(data.faces.length, 'face')} in this photo.</p>
                )}
                <p className="mt-0.5 text-sm text-plum-soft">
                  {plural(counts.clear, 'clear match', 'clear matches')}, {counts.model.toLocaleString('en-IN')} decided by {MODEL_NAME}, {plural(counts.new, 'new person', 'new people')}.
                  {data.took_ms !== null ? ` Took ${(data.took_ms / 1000).toFixed(1)} seconds.` : ''}
                </p>
              </section>
              <section aria-label={MODEL_NAME}>
                <h3 className="text-sm font-bold">{MODEL_NAME}</h3>
                <div className="mt-0.5">
                  <Description data={data} onDescribed={(answer) => query.mutate((current) => ({ ...current, description: answer }))} />
                </div>
                {data.model_questions !== null ? (
                  <p className="mt-1 text-sm text-plum-soft">
                    {data.model_questions === 0
                      ? 'No close faces here, so it was not asked about any.'
                      : `Asked about close faces ${plural(data.model_questions, 'time')}.`}
                  </p>
                ) : null}
              </section>
              <section aria-label="Faces">
                <h3 className="text-sm font-bold">Faces</h3>
                {data.faces.length === 0 ? <p className="mt-0.5 text-sm text-plum-soft">No face was clear enough to read.</p> : null}
                <ul className="mt-1 max-h-[46dvh] space-y-1 overflow-y-auto pr-1">
                  {data.faces.map((face) => (
                    <li key={face.id}>
                      <button
                        type="button"
                        aria-pressed={selected === face.id}
                        onClick={() => choose(face)}
                        className={`flex w-full items-start gap-2.5 rounded-xl p-1.5 text-left ${selected === face.id ? 'bg-primary-wash' : 'hover:bg-plum/5'}`}
                      >
                        <span className="relative shrink-0">
                          {/* eslint-disable-next-line @next/next/no-img-element -- a small crop from the API */}
                          <img src={face.crop_url ?? ''} alt="" loading="lazy" className="size-11 rounded-full object-cover" />
                          <span className={`absolute -left-1 -top-1 rounded-sm px-1 text-[0.65rem] font-bold leading-4 ${TAG[toneOf(face)]} ring-1 ring-plum/30`}>{face.number}</span>
                        </span>
                        <span className="min-w-0 text-sm">
                          <span className={`block truncate font-semibold ${face.name ? 'text-plum' : 'text-plum-soft'}`}>
                            {personName(face)}
                            {face.frame_ms !== null ? <span className="font-normal text-plum-soft"> at {formatDuration(face.frame_ms)}</span> : null}
                          </span>
                          <span className="block text-plum-soft">{decision(face)}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              <p className="border-t border-sand pt-3 text-sm text-plum-soft">
                OpenCV scores from 0 to 1. {score(data.rules.match_at)} or more is the same person, below {score(data.rules.ask_at)} is not, and between them {MODEL_NAME} decides.
              </p>
            </div>
          </div>
        ) : null}
      </div>
    </dialog>
  )
}
