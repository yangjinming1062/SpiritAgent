import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { beginConversationInputActivity } from '@/modules/conversation'
import { setVoiceRecording } from '@/modules/speech'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { mediaApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

export interface VoiceSample {
  sample_id: string
  url: string
  duration: number
  mime_type: string
}

const MAX_SAMPLE_BYTES = 24 * 1024 * 1024

function asDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result)
      } else {
        reject(new Error('Could not read audio'))
      }
    }

    reader.onerror = () => reject(reader.error ?? new Error('Could not read audio'))
    reader.readAsDataURL(blob)
  })
}

export function useVoiceSample(active: boolean) {
  const auth = useStore($auth)
  const t = useStrings().voice
  const begin = useAsyncGuard()
  const activeRef = useLatestRef(active)
  const [sample, setSample] = useState<VoiceSample | null>(null)
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const finishActivity = useRef<(() => void) | null>(null)

  const release = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }

    const recorder = recorderRef.current
    recorderRef.current = null

    if (recorder) {
      recorder.ondataavailable = null
      recorder.onstop = null
      recorder.onerror = null
      recorder.stream.getTracks().forEach(track => track.stop())

      if (recorder.state !== 'inactive') {
        recorder.stop()
      }
    }

    finishActivity.current?.()
    finishActivity.current = null
    setVoiceRecording(false)
  }, [])

  const cancel = useCallback(() => {
    generation.current++
    release()
    setRecording(false)
    setBusy(false)
  }, [release])

  useEffect(() => {
    if (!active) {
      cancel()
    }

    return cancel
  }, [active, auth, cancel])

  const upload = async (blob: Blob, filename: string): Promise<void> => {
    if (!activeRef.current) {
      return
    }

    cancel()
    setSample(null)
    setError(null)

    if (blob.size === 0 || blob.size > MAX_SAMPLE_BYTES) {
      setError(blob.size === 0 ? t.emptyAudio : t.audioTooLarge)

      return
    }

    const currentAuth = $auth.get()
    const authCurrent = captureAuthScope()

    if (currentAuth.kind !== 'authenticated' || !authCurrent) {
      return
    }

    const operation = ++generation.current
    const mounted = begin()
    const isLive = (): boolean => mounted() && authCurrent() && activeRef.current && operation === generation.current
    const finish = beginConversationInputActivity('uploading')
    finishActivity.current = finish
    setBusy(true)

    try {
      const dataUrl = await asDataUrl(blob)

      if (!isLive()) {
        return
      }

      const result = await mediaApi().media.uploadVoiceSample({
        authSessionId: currentAuth.snapshot.sessionId,
        dataUrl,
        filename
      })

      if (isLive()) {
        setSample(result)
      }
    } catch (failure) {
      log.warn('voice-sample', 'Voice sample upload failed', failure)

      if (isLive()) {
        setError(errorMessage(failure, t.uploadFailed))
      }
    } finally {
      finish()

      if (finishActivity.current === finish) {
        finishActivity.current = null
      }

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const uploadRef = useLatestRef(upload)

  const stop = (): void => {
    const recorder = recorderRef.current

    if (recorder?.state === 'recording') {
      recorder.stop()
      recorder.stream.getTracks().forEach(track => track.stop())
    }
  }

  const start = async (): Promise<void> => {
    if (!activeRef.current || busy) {
      return
    }

    cancel()
    setSample(null)
    setError(null)
    const mounted = begin()
    const authCurrent = captureAuthScope()

    if (!authCurrent) {
      return
    }

    const operation = ++generation.current
    const isLive = (): boolean => mounted() && authCurrent() && activeRef.current && operation === generation.current
    const finish = beginConversationInputActivity('recording')
    finishActivity.current = finish
    setBusy(true)
    setVoiceRecording(true)
    let stream: MediaStream | null = null

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })

      if (!isLive()) {
        stream.getTracks().forEach(track => track.stop())

        return
      }

      const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(value =>
        MediaRecorder.isTypeSupported(value)
      )

      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      recorderRef.current = recorder
      const chunks: Blob[] = []

      recorder.ondataavailable = event => {
        if (event.data.size > 0) {
          chunks.push(event.data)
        }
      }

      recorder.onstop = () => {
        if (!isLive()) {
          return
        }

        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' })
        release()
        setRecording(false)
        const extension = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'
        void uploadRef.current(blob, `voice.${extension}`)
      }

      recorder.onerror = () => {
        if (isLive()) {
          cancel()
          setError(t.micUnavailable)
        }
      }

      recorder.start()
      setRecording(true)
      timer.current = setTimeout(stop, 60_000)
    } catch (failure) {
      stream?.getTracks().forEach(track => track.stop())
      log.warn('voice-sample', 'Voice sample recording failed', failure)

      if (isLive()) {
        cancel()
        setError(t.micUnavailable)
      }
    }
  }

  return { sample, recording, busy, error, start, stop, upload, cancel }
}
