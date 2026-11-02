'use client'

import { Camera, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

/**
 * The live selfie camera. Some browsers refuse it (inside a mail or chat app, say); the person is
 * then offered their phone's own camera instead, through a tap the browser accepts.
 */
export function CameraDialog({
  onCapture,
  onClose,
  onUsePhoneCamera,
}: {
  onCapture: (file: File) => void
  onClose: () => void
  onUsePhoneCamera: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const [refused, setRefused] = useState(false)

  useEffect(() => {
    const element = dialog.current
    if (element && !element.open) element.showModal()
    let stream: MediaStream | null = null
    let alive = true
    navigator.mediaDevices
      .getUserMedia({ audio: false, video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 1280 } } })
      .then((granted) => {
        if (!alive) {
          granted.getTracks().forEach((track) => track.stop())
          return
        }
        stream = granted
        if (video.current) video.current.srcObject = granted
      })
      .catch(() => {
        if (alive) setRefused(true)
      })
    return () => {
      alive = false
      stream?.getTracks().forEach((track) => track.stop())
      if (element?.open) element.close()
    }
  }, [])

  const capture = () => {
    const source = video.current
    if (!source || !source.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = source.videoWidth
    canvas.height = source.videoHeight
    canvas.getContext('2d')?.drawImage(source, 0, 0)
    canvas.toBlob(
      (blob) => {
        if (blob) onCapture(new File([blob], 'selfie.jpg', { type: 'image/jpeg' }))
      },
      'image/jpeg',
      0.92,
    )
  }

  return (
    <dialog
      ref={dialog}
      className="gallery-camera"
      aria-label="Take a selfie"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <button type="button" className="gallery-round gallery-camera-close" aria-label="Close the camera" onClick={onClose}>
        <X aria-hidden />
      </button>
      {refused ? (
        <div className="gallery-camera-refused">
          <p>This browser did not open the camera. Allow the camera in its settings, or use your phone&apos;s own camera.</p>
          <button type="button" className="gallery-cta" onClick={onUsePhoneCamera}>
            <Camera aria-hidden />
            Open my phone&apos;s camera
          </button>
        </div>
      ) : (
        <>
          <div className="gallery-camera-frame">
            <video ref={video} autoPlay playsInline muted onLoadedMetadata={() => setReady(true)} />
            <span aria-hidden className="gallery-camera-guide" />
          </div>
          <p className="gallery-camera-hint">Keep your face inside the circle, in good light.</p>
          <button type="button" className="gallery-shutter" aria-label="Take the photo" disabled={!ready} onClick={capture} />
        </>
      )}
    </dialog>
  )
}
