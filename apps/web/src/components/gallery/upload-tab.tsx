'use client'

import { Check, Film, ImagePlus, LoaderCircle, RotateCcw, Upload, X } from 'lucide-react'
import { useRef, useState, useSyncExternalStore } from 'react'
import { sizeLabel, type QueueItem, type UploadQueue } from '@/lib/gallery-uploads'
import { goTo } from './route'

/** How many finished files stay listed. Files in progress and failed ones are always listed. */
const SHOWN_DONE = 24

export function UploadTab({ queue, limits }: { queue: UploadQueue; limits: { image: number; video: number } }) {
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const done = items.filter((item) => item.state === 'done').length
  const failed = items.filter((item) => item.state === 'failed').length
  const active = items.length - done - failed
  const finished = items.filter((item) => item.state === 'done').slice(0, SHOWN_DONE)
  const shown = [...items.filter((item) => item.state !== 'done'), ...finished]

  const addFiles = (files: FileList | null | undefined) => {
    if (files?.length) queue.add(Array.from(files), limits)
  }

  return (
    <div className="gallery-upload" data-filled={items.length ? '' : undefined}>
      <div
        className="gallery-drop"
        data-over={over ? '' : undefined}
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setOver(false)
          addFiles(event.dataTransfer.files)
        }}
      >
        <span className="gallery-drop-icon">
          <ImagePlus aria-hidden strokeWidth={1.4} />
        </span>
        <h2>Share your photos and videos</h2>
        <p className="gallery-drop-laptop">Drop them here, or choose them from this computer.</p>
        <p className="gallery-drop-phone">Everything you shot at the event, in one go.</p>
        <button type="button" className="gallery-cta" onClick={() => input.current?.click()}>
          <Upload aria-hidden />
          Choose photos and videos
        </button>
        <p className="gallery-note">
          Photos up to {sizeLabel(limits.image)}, videos up to {sizeLabel(limits.video)}. They are shared with everyone who has the link.
        </p>
        <input
          ref={input}
          type="file"
          accept="image/*,video/*"
          multiple
          hidden
          aria-label="Choose photos and videos"
          onChange={(event) => {
            addFiles(event.target.files)
            event.target.value = ''
          }}
        />
      </div>

      {items.length ? (
        <section className="gallery-queue" aria-label="Your uploads">
          <div className="gallery-queue-head" role="status">
            {active ? (
              <p>
                <strong>
                  {done.toLocaleString('en-IN')} of {items.length.toLocaleString('en-IN')} uploaded.
                </strong>{' '}
                Keep this page open until they finish.
              </p>
            ) : (
              <p>
                <strong>
                  {done.toLocaleString('en-IN')} {done === 1 ? 'file' : 'files'} uploaded.
                </strong>{' '}
                {done ? `Thank you. ${done === 1 ? 'It appears' : 'They appear'} under People in a little while.` : null}
              </p>
            )}
            {!active && done ? (
              <button type="button" className="gallery-ghost" onClick={() => goTo({ tab: 'people', person: null, item: null })}>
                Open People
              </button>
            ) : null}
          </div>
          <ul className="gallery-queue-list">
            {shown.map((item) => (
              <QueueRow key={item.id} item={item} queue={queue} />
            ))}
          </ul>
          {done > SHOWN_DONE ? <p className="gallery-note">The latest {SHOWN_DONE} are listed here.</p> : null}
        </section>
      ) : null}
    </div>
  )
}

function QueueRow({ item, queue }: { item: QueueItem; queue: UploadQueue }) {
  const percent = item.size ? Math.min(100, Math.floor((item.sent / item.size) * 100)) : 0
  return (
    <li className="gallery-row" data-state={item.state}>
      <span className="gallery-row-thumb">
        {item.preview ? (
          // eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen file
          <img src={item.preview} alt="" loading="lazy" decoding="async" />
        ) : item.isVideo ? (
          <Film aria-hidden strokeWidth={1.5} />
        ) : (
          <ImagePlus aria-hidden strokeWidth={1.5} />
        )}
      </span>
      <span className="gallery-row-text">
        <span className="gallery-row-name">{item.name}</span>
        {item.state === 'failed' ? (
          <span className="gallery-row-error">{item.error}</span>
        ) : item.state === 'done' ? (
          <span className="gallery-row-done">Uploaded</span>
        ) : item.state === 'sending' ? (
          <span className="gallery-row-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={`Uploading ${item.name}`}>
            <span style={{ width: `${percent}%` }} />
          </span>
        ) : (
          <span className="gallery-row-wait">Waiting</span>
        )}
      </span>
      <span className="gallery-row-action">
        {item.state === 'done' ? (
          <Check aria-hidden className="gallery-row-tick" />
        ) : item.state === 'sending' ? (
          <span className="gallery-row-percent" aria-hidden>
            {percent === 100 ? <LoaderCircle className="animate-spin" /> : `${percent}%`}
          </span>
        ) : null}
        {item.state === 'failed' ? (
          <button type="button" className="gallery-round" aria-label={`Try ${item.name} again`} onClick={() => queue.retry(item.id)}>
            <RotateCcw aria-hidden />
          </button>
        ) : null}
        {item.state !== 'done' ? (
          <button type="button" className="gallery-round" aria-label={`Remove ${item.name}`} onClick={() => queue.remove(item.id)}>
            <X aria-hidden />
          </button>
        ) : null}
      </span>
    </li>
  )
}
