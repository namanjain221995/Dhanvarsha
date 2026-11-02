'use client'

import { Camera, ImagePlus, LoaderCircle } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { ApiError } from '@/lib/api'
import { galleryApi, prepareSelfie, type GalleryPerson } from '@/lib/gallery'
import { CameraDialog } from './camera'
import { Coin } from './coin'

const NAME_MAX = 80

/**
 * The selfie and the name: the first thing anyone fills in, and how they change either later. A
 * selfie is the only photo kept out of the gallery: only a small round crop of it stays.
 */
export function SelfieForm({
  initialName,
  currentAvatar = null,
  token,
  submitLabel,
  onDone,
}: {
  initialName: string
  currentAvatar?: string | null
  token: string | null
  submitLabel: string
  onDone: (person: GalleryPerson) => void
}) {
  const id = useId()
  const [name, setName] = useState(initialName)
  const [consent, setConsent] = useState(false)
  const [selfie, setSelfie] = useState<{ file: File; url: string } | null>(null)
  const [unreadable, setUnreadable] = useState(false)
  const [camera, setCamera] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<{ selfie?: string; name?: string; consent?: string; form?: string }>({})
  const chooser = useRef<HTMLInputElement>(null)
  const phoneCamera = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const url = selfie?.url
    return () => {
      if (url) URL.revokeObjectURL(url)
    }
  }, [selfie?.url])

  const choose = (file: File | undefined) => {
    if (!file) return
    setSelfie({ file, url: URL.createObjectURL(file) })
    setUnreadable(false)
    setErrors((previous) => ({ ...previous, selfie: undefined, form: undefined }))
  }

  const takeSelfie = () => {
    if (typeof navigator.mediaDevices?.getUserMedia === 'function') setCamera(true)
    else phoneCamera.current?.click()
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const found: typeof errors = {}
    if (!selfie) found.selfie = 'Take a selfie or upload a photo of yourself.'
    if (!name.trim()) found.name = 'Enter your name.'
    if (!consent) found.consent = 'Tick the box to agree, so we can match your face in the photos.'
    setErrors(found)
    if (!selfie || Object.keys(found).length) return
    setBusy(true)
    try {
      const form = new FormData()
      form.set('name', name.trim())
      form.set('consent', 'true')
      if (token) form.set('token', token)
      form.set('selfie', await prepareSelfie(selfie.file))
      const answer = await galleryApi<{ person: GalleryPerson }>('/enrol', { method: 'POST', form, timeoutMs: 90_000 })
      onDone(answer.person)
    } catch (error) {
      if (error instanceof ApiError && ['NO_FACE', 'FACE_TOO_SMALL', 'INVALID_IMAGE', 'FILE_TOO_LARGE'].includes(error.code)) {
        setErrors({ selfie: error.message })
      } else {
        const fields = error instanceof ApiError ? (error.details.fields as Record<string, string> | undefined) : undefined
        setErrors(fields ?? { form: error instanceof ApiError ? error.message : 'Could not save this. Try again.' })
      }
      setBusy(false)
    }
  }

  const shown = selfie && !unreadable ? selfie.url : currentAvatar
  return (
    <form noValidate onSubmit={(event) => void submit(event)} className="gallery-form">
      <div className="gallery-selfie" data-chosen={selfie ? '' : undefined}>
        <div className="gallery-selfie-coin" key={selfie?.url ?? 'blank'}>
          {shown ? (
            <span className="gallery-coin" data-size="large" data-spin={selfie ? '' : undefined}>
              <span className="gallery-coin-face">
                {/* eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen photo */}
                <img src={shown} alt={selfie ? 'Your selfie' : 'Your picture now'} onError={() => setUnreadable(true)} />
              </span>
            </span>
          ) : selfie ? (
            <Coin src={null} size="large" spin />
          ) : (
            <span className="gallery-coin-blank">
              <Camera aria-hidden strokeWidth={1.4} />
            </span>
          )}
        </div>
        {selfie && unreadable ? <p className="gallery-note">{selfie.file.name} is ready to send.</p> : null}
        <div className="gallery-selfie-actions">
          <button type="button" className={selfie || currentAvatar ? 'gallery-ghost' : 'gallery-cta'} onClick={takeSelfie}>
            <Camera aria-hidden />
            {selfie ? 'Take another' : 'Take a selfie'}
          </button>
          <button type="button" className="gallery-ghost" onClick={() => chooser.current?.click()}>
            <ImagePlus aria-hidden />
            Upload a photo
          </button>
        </div>
        <input
          ref={chooser}
          type="file"
          accept="image/*"
          hidden
          aria-label="Upload a photo of yourself"
          onChange={(event) => {
            choose(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <input
          ref={phoneCamera}
          type="file"
          accept="image/*"
          capture="user"
          hidden
          aria-label="Take a selfie with your phone's camera"
          onChange={(event) => {
            choose(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        {errors.selfie ? (
          <p role="alert" className="gallery-error">
            {errors.selfie}
          </p>
        ) : null}
      </div>

      <div className="gallery-field">
        <label htmlFor={`${id}-name`}>Your name</label>
        <input
          id={`${id}-name`}
          value={name}
          maxLength={NAME_MAX}
          autoComplete="name"
          enterKeyHint="done"
          aria-invalid={errors.name ? true : undefined}
          aria-describedby={errors.name ? `${id}-name-error` : undefined}
          onChange={(event) => setName(event.target.value)}
        />
        {errors.name ? (
          <p id={`${id}-name-error`} role="alert" className="gallery-error">
            {errors.name}
          </p>
        ) : null}
      </div>

      <div className="gallery-consent">
        <label>
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>
            I agree that my selfie is used to find my face in the event&apos;s photos and videos. Anyone with this link can see my name
            and those photos. I can remove myself at any time.
          </span>
        </label>
        {errors.consent ? (
          <p role="alert" className="gallery-error">
            {errors.consent}
          </p>
        ) : null}
      </div>

      {errors.form ? (
        <p role="alert" className="gallery-error">
          {errors.form}
        </p>
      ) : null}

      <button type="submit" className="gallery-cta" disabled={busy}>
        {busy ? <LoaderCircle aria-hidden className="animate-spin" /> : null}
        {busy ? 'Finding your face' : submitLabel}
      </button>

      {camera ? (
        <CameraDialog
          onClose={() => setCamera(false)}
          onCapture={(file) => {
            choose(file)
            setCamera(false)
          }}
          onUsePhoneCamera={() => {
            setCamera(false)
            phoneCamera.current?.click()
          }}
        />
      ) : null}
    </form>
  )
}
