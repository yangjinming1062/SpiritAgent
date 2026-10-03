import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { presentationPorts } from '@/shared/presentation-ports'
import { getSpiritAgentConfig } from '@/shared/spiritagent'
import { getStrings } from '@/shared/strings'

import { IM_VOICE_BAR_AUDIO_CONSTRAINTS } from './audio-constraints'
import { convertBlobToWav } from './audio-wav'
import { blobToDataUrl } from './blob-data-url'
import type { ConversationRuntime } from './chat-runtime'
import { activeConversationRuntime } from './chat-store'
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

let recordingOwner: symbol | null = null

type Options = {
  runtime?: ConversationRuntime
  eligible?: boolean
  isReadOnlySession?: boolean
}

// 语音消息生命周期管理：录音、自动停止、全局事件解绑、音轨清理与转写提交。

export function useVoiceRecorder({
  isReadOnlySession,
  runtime = activeConversationRuntime(),
  eligible = true
}: Options): {
  recording: boolean
  start: () => void
  stop: () => Promise<void>
} {
  const { markAssistantTerminal, pushPendingPrompt, pushUserMessage, schedulePendingFlush } = runtime
  const [recording, setRecording] = useState(false)
  const ownerToken = useRef(Symbol('recording-view'))
  const scopeRef = useRef({ runtime, eligible })
  scopeRef.current = { runtime, eligible }

  const isCurrent = useCallback(
    (): boolean =>
      !unmountedRef.current && runtime.isCurrent() && scopeRef.current.runtime === runtime && scopeRef.current.eligible,
    [runtime]
  )

  const recordingRef = useRef<{ recorder: MediaRecorder; chunks: Blob[] } | null>(null)
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const configRef = useRef<{ voice?: { max_recording_seconds?: number } }>({})
  const startPendingRef = useRef<Promise<void> | null>(null)
  const stopRef = useRef<() => Promise<void>>(async () => {})
  const unmountedRef = useRef(false)
  const operationRef = useRef(0)

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

    if (recordingOwner === ownerToken.current) {
      recordingOwner = null
      conversationVoiceSink().setRecording(false)
    }
  }

  const transcribe = useCallback(
    async (blob: Blob, operation: number): Promise<string | null> => {
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
        if (!isCurrent() || operation !== operationRef.current) {
          return null
        }

        const res = await window.spiritagent.media.stt({ dataUrl, filename: `voice.${ext}` })
        const text = (res.text ?? '').trim()

        return text || null
      } catch (err: unknown) {
        log.warn('voice-recorder', 'Transcription failed:', err)
        const voiceInput = getStrings().chat.voiceInput

        if (isCurrent() && operation === operationRef.current) {
          markAssistantTerminal({ error: isMediaBusyError(err) ? voiceInput.busy : voiceInput.notRecognized })
        }

        return null
      }
    },
    [isCurrent, markAssistantTerminal]
  )

  const stop = useCallback(async () => {
    const operation = operationRef.current
    const recordingCurrent = (): boolean => isCurrent() && operation === operationRef.current

    if (startPendingRef.current) {
      try {
        await startPendingRef.current
      } catch {
        /* 由 start 抛出 */
      }
    }

    if (!recordingCurrent()) {
      return
    }

    cancelAutoStop()

    const capture = recordingRef.current

    if (!capture || capture.recorder.state === 'inactive') {
      return
    }

    const { recorder, chunks } = capture
    const mimeType = recorder.mimeType || getSupportedOpusMimeType() || 'audio/webm'

    const blob = await new Promise<Blob | null>(resolve => {
      recorder.onstop = () => {
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

    if (!recordingCurrent()) {
      return
    }

    recordingRef.current = null
    endRecording()

    if (!blob || blob.size === 0) {
      presentationPorts().setSpriteState('idle')

      return
    }

    presentationPorts().setSpriteState('thinking')
    const text = await transcribe(blob, operation)

    if (!recordingCurrent()) {
      return
    }

    if (text) {
      if (isReadOnlySession) {
        presentationPorts().setSpriteState('idle', { force: true })

        return
      }

      try {
        await ensureChatSession(runtime)

        if (!recordingCurrent()) {
          return
        }

        pushUserMessage(text)
        presentationPorts().setSpriteState('thinking')
        pushPendingPrompt({ text })
        schedulePendingFlush()
      } catch (err) {
        log.warn('voice-recorder', 'Voice message send failed:', err)

        if (!recordingCurrent()) {
          return
        }

        presentationPorts().setSpriteState('idle', { force: true })
        markAssistantTerminal({ error: errorMessage(err, getStrings().chat.sendFailed) })
      }
    } else {
      presentationPorts().setSpriteState('idle', { force: true })
    }
  }, [
    isReadOnlySession,
    runtime,
    markAssistantTerminal,
    pushPendingPrompt,
    pushUserMessage,
    schedulePendingFlush,
    isCurrent,
    transcribe
  ])

  stopRef.current = stop

  const start = useCallback(() => {
    if (
      !isCurrent() ||
      isReadOnlySession ||
      recordingOwner !== null ||
      startPendingRef.current ||
      recordingRef.current !== null
    ) {
      return
    }

    recordingOwner = ownerToken.current
    const operation = ++operationRef.current
    conversationVoiceSink().setRecording(true)
    let pending: Promise<void> | null = null
    pending = (async () => {
      let stream: MediaStream | null = null

      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: IM_VOICE_BAR_AUDIO_CONSTRAINTS })

        // 等待麦克风期间失去资格，即使重新活动也不能恢复旧录音。
        if (!isCurrent() || operation !== operationRef.current) {
          stopTracks(stream)
          endRecording()

          return
        }

        const mimeType = getSupportedOpusMimeType()
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)

        const chunks: Blob[] = []
        recordingRef.current = { recorder, chunks }

        recorder.ondataavailable = e => {
          if (e.data.size > 0) {
            chunks.push(e.data)
          }
        }

        setRecording(true)
        presentationPorts().setSpriteState('listening')
        recorder.start()

        const cap = configRef.current.voice?.max_recording_seconds ?? 60

        if (cap > 0) {
          autoStopRef.current = setTimeout(() => {
            if (recordingRef.current?.recorder.state === 'recording') {
              void stopRef.current()
            }
          }, cap * 1000)
        }
      } catch (err) {
        log.warn('voice-recorder', 'Recording failed to start:', err)
        // 构造或启动录音失败都要就地停轨并复位，否则麦克风指示灯常亮、按钮停在录音态。
        stopTracks(stream)

        if (isCurrent() && operation === operationRef.current && recordingOwner === ownerToken.current) {
          recordingRef.current = null
          endRecording()
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
  }, [isReadOnlySession, markAssistantTerminal, isCurrent])

  useEffect(() => {
    if (eligible) {
      return
    }

    operationRef.current++
    const recorder = recordingRef.current?.recorder
    recordingRef.current = null

    if (recorder) {
      recorder.ondataavailable = null
      stopTracks(recorder.stream)

      if (recorder.state !== 'inactive') {
        recorder.stop()
      }
    }

    cancelAutoStop()
    endRecording()
  }, [eligible])

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
    const token = ownerToken.current
    const operations = operationRef

    return () => {
      unmountedRef.current = true
      operations.current++
      cancelAutoStop()
      const recorder = recordingRef.current?.recorder
      recordingRef.current = null

      if (recorder) {
        recorder.ondataavailable = null
        stopTracks(recorder.stream)

        if (recorder.state !== 'inactive') {
          recorder.stop()
        }
      }

      setRecording(false)

      if (recordingOwner === token) {
        recordingOwner = null
        conversationVoiceSink().setRecording(false)
      }
    }
  }, [runtime])

  return { recording, start, stop }
}
