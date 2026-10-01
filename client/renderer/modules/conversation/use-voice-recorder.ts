import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { presentationPorts } from '@/shared/presentation-ports'
import { getSpiritAgentConfig } from '@/shared/spiritagent'
import { getStrings } from '@/shared/strings'

import { IM_VOICE_BAR_AUDIO_CONSTRAINTS } from './audio-constraints'
import { convertBlobToWav } from './audio-wav'
import { blobToDataUrl } from './blob-data-url'
import { markAssistantTerminal, pushPendingPrompt, pushUserMessage, schedulePendingFlush } from './chat-store'
import { ensureChatSession } from './session-list-store'
import { conversationVoiceSink } from './voice-link'

// 语音消息用 MediaRecorder 整段录制（webm/opus）→ 客户端转 16kHz WAV → REST 转写。
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
  return BUSY_ERROR_PATTERN.test(errorMessage(err))
}

function stopTracks(stream: MediaStream | null): void {
  stream?.getTracks().forEach(track => track.stop())
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
  const unmountedRef = useRef(false)

  useEffect(() => {
    void getSpiritAgentConfig()
      .then(c => {
        configRef.current = { voice: c.voice }
      })
      .catch((error: unknown) => {
        // 读取失败时按默认时长上限录音。
        log.warn('voice-recorder', 'Could not load recording limit', error)
        configRef.current = {}
      })
  }, [])

  const cancelAutoStop = () => {
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }

  // 结束录音态；卸载后不再回写语音链路。
  const endRecording = () => {
    setRecording(false)

    if (!unmountedRef.current) {
      conversationVoiceSink().setRecording(false)
    }
  }

  const transcribe = async (blob: Blob): Promise<string | null> => {
    try {
      let finalBlob = blob

      try {
        finalBlob = await convertBlobToWav(blob, 16000)
      } catch (convErr) {
        log.warn('voice-recorder', 'Failed to convert audio to wav, fallback to raw blob:', convErr)
      }

      const dataUrl = await blobToDataUrl(finalBlob)

      const ext = getAudioExtensionForMime(finalBlob.type)
      // 不指定语言：主进程按当前用户语言设置转写。
      const res = await window.spiritagent.media.stt({ dataUrl, filename: `voice.${ext}` })
      const text = (res.text ?? '').trim()

      return text || null
    } catch (err: unknown) {
      log.warn('voice-recorder', 'Transcription failed:', err)
      const voiceInput = getStrings().chat.voiceInput
      markAssistantTerminal({ error: isMediaBusyError(err) ? voiceInput.busy : voiceInput.notRecognized })

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
      endRecording()

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

    stopTracks(recorder.stream)
    streamRef.current = null
    endRecording()

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
        log.warn('voice-recorder', 'Voice message send failed:', err)
        presentationPorts().setSpriteState('idle', { force: true })
        markAssistantTerminal({ error: errorMessage(err, getStrings().chat.sendFailed) })
      }
    } else {
      presentationPorts().setSpriteState('idle', { force: true })
    }
  }, [isReadOnlySession])

  stopRef.current = stop

  const start = useCallback(() => {
    if (startPendingRef.current || recorderRef.current?.state === 'recording') {
      return
    }

    conversationVoiceSink().setRecording(true)
    let pending: Promise<void> | null = null
    pending = (async () => {
      let stream: MediaStream | null = null

      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: IM_VOICE_BAR_AUDIO_CONSTRAINTS })

        // 等待麦克风期间已卸载：不再开录，也就不会自动发送。
        if (unmountedRef.current) {
          stopTracks(stream)

          return
        }

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
      } catch (err) {
        log.warn('voice-recorder', 'Recording failed to start:', err)
        // 构造或启动录音失败都要就地停轨并复位，否则麦克风指示灯常亮、按钮停在录音态。
        stopTracks(stream)
        streamRef.current = null
        recorderRef.current = null
        chunksRef.current = []
        setRecording(false)

        if (!unmountedRef.current) {
          conversationVoiceSink().setRecording(false)
          markAssistantTerminal({ error: getStrings().chat.voiceInput.micUnavailable })
          presentationPorts().setSpriteState('idle')
        }
      } finally {
        if (startPendingRef.current === pending) {
          startPendingRef.current = null
        }
      }
    })()
    startPendingRef.current = pending
  }, [])

  // `recording` 切换驱动一个全局 mouseup 监听器，用户可在屏幕任意位置松开按钮即可停止录音。
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
    unmountedRef.current = false

    return () => {
      unmountedRef.current = true
      cancelAutoStop()
      const recorder = recorderRef.current

      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        stopTracks(recorder.stream)
        recorder.stop()
      }

      setRecording(false)
      conversationVoiceSink().setRecording(false)
    }
  }, [])

  return { recording, start, stop }
}
