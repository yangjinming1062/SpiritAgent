import { useCallback, useEffect, useRef, useState } from 'react'

import { presentationPorts } from '@/shared/presentation-ports'
import { getSpiritAgentConfig } from '@/shared/spiritagent'

import { IM_VOICE_BAR_AUDIO_CONSTRAINTS } from './audio-constraints'
import { convertBlobToWav } from './audio-wav'
import { markAssistantTerminal, pushPendingPrompt, pushUserMessage, schedulePendingFlush } from './chat-store'
import { ensureChatSession } from './session-list-store'

// IM 语音条仍走 MediaRecorder（webm/opus 整段录制 → 客户端转 16kHz WAV → REST 转写）。
const PREFERRED_OPUS_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/ogg',
  'audio/mp4;codecs=opus',
  'audio/mp4'
] as const

function getSupportedOpusMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return undefined
  }

  return PREFERRED_OPUS_MIME_TYPES.find(type => MediaRecorder.isTypeSupported(type))
}

function getAudioExtensionForMime(mime: string): string {
  if (mime.includes('wav')) {
    return 'wav'
  }

  if (mime.includes('mp3') || mime.includes('mpeg')) {
    return 'mp3'
  }

  if (mime.includes('ogg')) {
    return 'ogg'
  }

  if (mime.includes('mp4')) {
    return 'mp4'
  }

  return 'webm'
}

// 嗅探后端/Runner 抛出的"忙/背压/限流"消息，用于给用户区别提示。
const BUSY_ERROR_PATTERN = /busy|backpressure|rate limit/i

function isMediaBusyError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err)

  return BUSY_ERROR_PATTERN.test(msg)
}

type Options = {
  isReadOnlySession?: boolean
}

// 语音消息生命周期管理：录音、自动停止、全局事件解绑、音轨清理与转写提交。

export function useVoiceRecorder({ isReadOnlySession }: Options): {
  recording: boolean
  start: () => void
  stop: () => Promise<void>
} {
  const [recording, setRecording] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const configRef = useRef<{ voice?: { max_recording_seconds?: number } }>({})
  const startPendingRef = useRef<Promise<void> | null>(null)
  const stopRef = useRef<() => Promise<void>>(async () => {})

  useEffect(() => {
    void getSpiritAgentConfig()
      .then(c => {
        configRef.current = { voice: c.voice }
      })
      .catch(() => {
        configRef.current = {}
      })
  }, [])

  const cancelAutoStop = () => {
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }

  const stopTracks = (recorder: MediaRecorder | null) => {
    if (!recorder) {
      return
    }

    try {
      recorder.stream.getTracks().forEach(t => t.stop())
    } catch {
      /* 已关闭 */
    }
  }

  const transcribe = async (blob: Blob): Promise<string | null> => {
    if (!blob || blob.size === 0) {
      return null
    }

    try {
      let finalBlob = blob

      try {
        finalBlob = await convertBlobToWav(blob, 16000)
      } catch (convErr) {
        console.warn('[voice-recorder] Failed to convert audio to wav, fallback to raw blob:', convErr)
      }

      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()

        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('read failed'))
        reader.readAsDataURL(finalBlob)
      })

      const ext = getAudioExtensionForMime(finalBlob.type)
      const res = await window.spiritagent.media.stt({ dataUrl, filename: `voice.${ext}`, language: 'zh' })
      const text = (res.text ?? '').trim()

      return text || null
    } catch (err: unknown) {
      markAssistantTerminal({ error: isMediaBusyError(err) ? '语音服务正忙，请稍候再试' : '没听清，用打字吧～' })

      return null
    }
  }

  const stop = useCallback(async () => {
    if (startPendingRef.current) {
      try {
        await startPendingRef.current
      } catch {
        /* 由 start 抛出 */
      }
    }

    cancelAutoStop()

    const recorder = recorderRef.current

    if (!recorder || recorder.state === 'inactive') {
      setRecording(false)

      return
    }

    const mimeType = recorder.mimeType || getSupportedOpusMimeType() || 'audio/webm'

    const blob = await new Promise<Blob | null>(resolve => {
      recorder.onstop = () => {
        const chunks = chunksRef.current
        chunksRef.current = []

        if (chunks.length === 0) {
          resolve(null)

          return
        }

        resolve(new Blob(chunks, { type: mimeType }))
      }

      try {
        recorder.stop()
      } catch {
        resolve(null)
      }
    })

    stopTracks(recorder)
    streamRef.current = null

    setRecording(false)

    if (!blob || blob.size === 0) {
      presentationPorts().setSpriteState('idle')

      return
    }

    presentationPorts().setSpriteState('thinking')
    const text = await transcribe(blob)

    if (text) {
      if (isReadOnlySession) {
        presentationPorts().setSpriteState('idle', { force: true })

        return
      }

      try {
        await ensureChatSession()
        pushUserMessage(text)
        presentationPorts().setSpriteState('thinking')
        pushPendingPrompt({ text })
        schedulePendingFlush()
      } catch (err) {
        presentationPorts().setSpriteState('idle', { force: true })
        markAssistantTerminal({ error: err instanceof Error ? err.message : '发送失败' })
      }
    } else {
      presentationPorts().setSpriteState('idle', { force: true })
    }
  }, [isReadOnlySession])

  stopRef.current = stop

  const start = useCallback(() => {
    let pending: Promise<void> | null = null
    pending = (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: IM_VOICE_BAR_AUDIO_CONSTRAINTS })
        const mimeType = getSupportedOpusMimeType()
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)

        streamRef.current = stream
        recorderRef.current = recorder
        chunksRef.current = []

        recorder.ondataavailable = e => {
          if (e.data.size > 0) {
            chunksRef.current.push(e.data)
          }
        }

        setRecording(true)
        presentationPorts().setSpriteState('listening')
        recorder.start()

        const cap = configRef.current.voice?.max_recording_seconds ?? 60

        if (cap > 0) {
          autoStopRef.current = setTimeout(() => {
            if (recorderRef.current?.state === 'recording') {
              void stopRef.current()
            }
          }, cap * 1000)
        }
      } catch {
        markAssistantTerminal({ error: '无法使用麦克风录制语音' })
        presentationPorts().setSpriteState('idle')
      } finally {
        if (startPendingRef.current === pending) {
          startPendingRef.current = null
        }
      }
    })()
    startPendingRef.current = pending
  }, [])

  // `recording` 切换驱动一个全局 mouseup 监听器，
  // 用户可在屏幕任意位置松开按钮即可停止录音。
  useEffect(() => {
    if (!recording) {
      return
    }

    const handleGlobalMouseUp = () => {
      void stopRef.current()
    }

    window.addEventListener('mouseup', handleGlobalMouseUp)

    return () => {
      window.removeEventListener('mouseup', handleGlobalMouseUp)
    }
  }, [recording])

  // 卸载清理：关闭音轨，避免 OS 级别麦克风指示灯保持亮起。
  useEffect(() => {
    return () => {
      cancelAutoStop()
      const recorder = recorderRef.current

      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        stopTracks(recorder)

        try {
          recorder.stop()
        } catch {
          /* 已停止 */
        }
      }

      setRecording(false)
    }
  }, [])

  return { recording, start, stop }
}
