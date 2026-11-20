import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText, type RemoteApi } from './api'
import { pauseMedia } from './ui'

interface Recording {
  controller: AbortController
  media: MediaRecorder | null
}

export function useVoiceRecording(
  api: RemoteApi,
  sessionId: string | null,
  onUploaded: (url: string) => void,
  onError: (error: string) => void
) {
  const active = useRef<Recording | null>(null)
  const [phase, setPhase] = useState<'idle' | 'recording' | 'uploading'>('idle')
  const recordingUnavailable =
    location.protocol !== 'https:'
      ? '录音需要 HTTPS 连接'
      : !window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder
        ? '当前浏览器不支持录音'
        : ''

  const releaseRecording = useCallback(() => {
    active.current?.controller.abort()
    active.current = null
    setPhase('idle')
  }, [])

  useEffect(() => {
    const hidden = () => {
      if (document.visibilityState !== 'visible') {
        releaseRecording()
      }
    }
    document.addEventListener('visibilitychange', hidden)
    return () => {
      document.removeEventListener('visibilitychange', hidden)
      releaseRecording()
    }
  }, [releaseRecording, sessionId])

  const recordVoice = async () => {
    if (active.current) {
      const media = active.current.media
      if (media?.state === 'recording') {
        media.stop()
      }
      return
    }
    if (!sessionId || recordingUnavailable) {
      onError(recordingUnavailable || '请先选择会话')
      return
    }
    pauseMedia()
    const recording: Recording = { controller: new AbortController(), media: null }
    active.current = recording
    const { signal } = recording.controller
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true })
      const stopTracks = () => mic.getTracks().forEach(track => track.stop())
      if (signal.aborted) {
        stopTracks()
        return
      }
      let timer: ReturnType<typeof setTimeout> | undefined
      const clean = () => {
        clearTimeout(timer)
        const media = recording.media
        if (media) {
          media.onstop = null
          media.onerror = null
          if (media.state !== 'inactive') {
            try {
              media.stop()
            } catch (error) {
              console.warn('录音停止失败', errorText(error))
            }
          }
        }
        stopTracks()
      }
      signal.addEventListener('abort', clean, { once: true })
      const format = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type =>
        MediaRecorder.isTypeSupported(type)
      )
      const media = new MediaRecorder(mic, format ? { mimeType: format } : undefined)
      recording.media = media
      const chunks: Blob[] = []
      media.ondataavailable = event => {
        if (event.data.size) {
          chunks.push(event.data)
        }
      }
      media.onerror = () => {
        if (!signal.aborted) {
          releaseRecording()
          onError('录音失败，请重新录制')
        }
      }
      media.onstop = () => {
        if (signal.aborted) {
          return
        }
        signal.removeEventListener('abort', clean)
        clean()
        recording.media = null
        setPhase('uploading')
        const form = new FormData()
        form.append(
          'file',
          new Blob(chunks, { type: media.mimeType }),
          media.mimeType.includes('mp4') ? 'recording.m4a' : 'recording.webm'
        )
        form.append('session_id', sessionId)
        void api
          .invoke('media.upload_chat_audio', { body: form }, { signal })
          .then(result => {
            if (!signal.aborted) {
              onUploaded(result.url)
            }
          })
          .catch(error => {
            if (!signal.aborted) {
              onError(errorText(error))
            }
          })
          .finally(() => {
            if (!signal.aborted) {
              releaseRecording()
            }
          })
      }
      media.start()
      setPhase('recording')
      timer = setTimeout(() => {
        if (media.state === 'recording') {
          media.stop()
        }
      }, 60_000)
    } catch (error) {
      if (!signal.aborted) {
        releaseRecording()
        throw error
      }
    }
  }

  return {
    recordVoice,
    releaseRecording,
    recording: phase === 'recording',
    transcribing: phase === 'uploading',
    recordingUnavailable
  }
}
