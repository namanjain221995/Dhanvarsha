'use client'

import { LoaderCircle, Upload, Users, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError } from '@/lib/api'
import { galleryApi, type GalleryMe, type GalleryPerson } from '@/lib/gallery'
import { UploadQueue } from '@/lib/gallery-uploads'
import { splitTitle } from '@/components/participant/stage-sign-in'
import { Coin } from './coin'
import { PeopleTab } from './people-tab'
import { PersonView } from './person-view'
import { goTo, useGalleryRoute } from './route'
import { SelfieForm } from './selfie-form'
import { UploadTab } from './upload-tab'

/*
 * The photo gallery (/gallery), for anyone with the link. The first visit is a form: a selfie and a
 * name, and the browser is remembered from then on. Someone who comes from the gallery email has a
 * token in the link that says who they are, so their name is filled in and a second device finds
 * the same page. After that: two tabs, Upload and People.
 */

function tokenInLink(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

function clearLinkToken() {
  if (window.location.hash) window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
}

type Loaded = { me: GalleryMe; token: string | null }

export function GalleryApp() {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const token = tokenInLink()
    const request = token
      ? galleryApi<GalleryMe>('/me', { method: 'POST', body: { token }, signal: controller.signal })
      : galleryApi<GalleryMe>('/me', { signal: controller.signal })
    request
      .then((me) => {
        document.title = `${me.programme.name} photos`
        if (me.person) clearLinkToken()
        setLoaded({ me, token })
        setProblem(null)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setProblem(error instanceof ApiError ? error.message : 'Could not open the gallery. Try again.')
      })
    return () => controller.abort()
  }, [attempt])

  if (!loaded) {
    return (
      <div className="gallery-page gallery-centre">
        {problem ? (
          <div className="gallery-empty" role="alert">
            <p>{problem}</p>
            <button type="button" className="gallery-ghost" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        ) : (
          <LoaderCircle role="status" aria-label="Opening the gallery" className="gallery-spinner animate-spin" />
        )}
      </div>
    )
  }

  const { me, token } = loaded
  const setPerson = (person: GalleryPerson | null) => {
    clearLinkToken()
    setLoaded({ me: { ...me, person, suggested_name: person ? person.name : '' }, token: null })
  }

  if (!me.person) return <FirstVisit me={me} token={token} onDone={setPerson} />
  return <GalleryHome me={me} person={me.person} onPerson={setPerson} />
}

function EventTitle({ name }: { name: string }) {
  const { title, year } = splitTitle(name)
  return (
    <p className="gallery-title">
      {title}
      {year ? <span>{year}</span> : null}
    </p>
  )
}

function FirstVisit({ me, token, onDone }: { me: GalleryMe; token: string | null; onDone: (person: GalleryPerson) => void }) {
  return (
    <div className="gallery-page gallery-first">
      <main>
        <EventTitle name={me.programme.name} />
        <h1>Find yourself in every photo</h1>
        <p className="gallery-lede">
          {me.known && me.suggested_name ? `Hi ${me.suggested_name}. ` : ''}
          Add a selfie and your name. Your photos and videos from the event, from everyone who shares theirs, come together on one page.
        </p>
        <SelfieForm initialName={me.suggested_name} token={token} submitLabel="Continue" onDone={onDone} />
      </main>
    </div>
  )
}

function GalleryHome({ me, person, onPerson }: { me: GalleryMe; person: GalleryPerson; onPerson: (person: GalleryPerson | null) => void }) {
  const route = useGalleryRoute()
  const [queue] = useState(() => new UploadQueue(me.direct_uploads))
  const [managing, setManaging] = useState(false)
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot)
  const sending = items.filter((item) => item.state === 'waiting' || item.state === 'sending').length
  const closeManage = useCallback(() => setManaging(false), [])
  const limits = { image: me.max_image_bytes, video: me.max_video_bytes }

  useEffect(() => {
    if (!sending) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [sending])

  return (
    <div className="gallery-page gallery-home">
      <header className="gallery-bar">
        <EventTitle name={me.programme.name} />
        <nav className="gallery-tabs" aria-label="Gallery">
          <button
            type="button"
            aria-current={route.tab === 'upload' ? 'page' : undefined}
            onClick={() => goTo({ tab: 'upload', person: null, item: null })}
          >
            <Upload aria-hidden />
            Upload
            {sending ? <span className="gallery-tab-badge">{sending > 99 ? '99+' : sending}</span> : null}
          </button>
          <button
            type="button"
            aria-current={route.tab === 'people' ? 'page' : undefined}
            onClick={() => goTo({ tab: 'people', person: null, item: null })}
          >
            <Users aria-hidden />
            People
          </button>
        </nav>
        <button type="button" className="gallery-me" aria-label={`${person.name}: your picture and name`} onClick={() => setManaging(true)}>
          <Coin src={person.avatar_url} size="small" />
        </button>
      </header>

      <main className="gallery-main">
        {/* The upload tab stays mounted: a choice of files in the browser must survive a look at People. */}
        <div hidden={route.tab !== 'upload'}>
          <UploadTab queue={queue} limits={limits} />
        </div>
        {route.tab === 'people' ? (
          route.person ? (
            <PersonView key={route.person} personId={route.person} itemId={route.item} onManage={() => setManaging(true)} />
          ) : (
            <PeopleTab />
          )
        ) : null}
      </main>

      {managing ? <ManageSheet person={person} onClose={closeManage} onPerson={onPerson} /> : null}
    </div>
  )
}

function ManageSheet({
  person,
  onClose,
  onPerson,
}: {
  person: GalleryPerson
  onClose: () => void
  onPerson: (person: GalleryPerson | null) => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [view, setView] = useState<'menu' | 'edit' | 'remove'>('menu')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    const element = dialog.current
    if (element && !element.open) element.showModal()
    return () => {
      if (element?.open) element.close()
    }
  }, [])

  const remove = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await galleryApi('/me', { method: 'DELETE' })
      onPerson(null)
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : 'Could not remove you. Try again.')
      setBusy(false)
    }
  }

  return (
    <dialog
      ref={dialog}
      className="gallery-sheet"
      aria-label="Your picture and name"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose()
      }}
    >
      <div className="gallery-sheet-body">
        <button type="button" className="gallery-round gallery-sheet-close" aria-label="Close" onClick={onClose}>
          <X aria-hidden />
        </button>
        {view === 'menu' ? (
          <div className="gallery-sheet-menu">
            <Coin src={person.avatar_url} size="large" />
            <h2>{person.name}</h2>
            <button
              type="button"
              className="gallery-cta"
              onClick={() => {
                goTo({ tab: 'people', person: person.id, item: null })
                onClose()
              }}
            >
              See my photos
            </button>
            <button type="button" className="gallery-ghost" onClick={() => setView('edit')}>
              Change my selfie or name
            </button>
            <button type="button" className="gallery-link" onClick={() => setView('remove')}>
              Remove me from the gallery
            </button>
          </div>
        ) : null}
        {view === 'edit' ? (
          <div className="gallery-sheet-edit">
            <h2>Change my selfie or name</h2>
            <SelfieForm
              initialName={person.name}
              currentAvatar={person.avatar_url}
              token={null}
              submitLabel="Save"
              onDone={(updated) => {
                onPerson(updated)
                onClose()
              }}
            />
          </div>
        ) : null}
        {view === 'remove' ? (
          <div className="gallery-sheet-menu">
            <h2>Remove me from the gallery?</h2>
            <p>
              This deletes your name, your picture and the record of your face, and your page goes away. Photos stay in the gallery, without
              your name on them. To have a photo removed, ask the organisers.
            </p>
            {problem ? (
              <p role="alert" className="gallery-error">
                {problem}
              </p>
            ) : null}
            <button type="button" className="gallery-danger" disabled={busy} onClick={() => void remove()}>
              {busy ? <LoaderCircle aria-hidden className="animate-spin" /> : null}
              Remove me
            </button>
            <button type="button" className="gallery-link" onClick={() => setView('menu')}>
              Keep me in the gallery
            </button>
          </div>
        ) : null}
      </div>
    </dialog>
  )
}
