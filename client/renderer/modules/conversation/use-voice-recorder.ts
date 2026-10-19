import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { blobToDataUrl } from '@/shared/lib/blob-data-url'
import { log } from '@/shared/lib/log'
import { mediaApi } from '@/shared/platform/desktop'
import { presentationPorts } from '@/shared/presentation-ports'
import { getSpiritAgentConfig } from '@/shared/spiritagent'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { CHAT_AUDIO_CONSTRAINTS } from './audio-constraints'
import type { ConversationRuntime } from './chat-runtime'
import { beginConversationInputActivity } from './conversation-input-busy'
import { ensureChatSession } from './session-list-store'
import { conversationVoiceSink } from './voice-link'

// 原始录音落到会话附件；后端优先原生音频，必要时转写。
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
    return 'm4a'
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
  runtime: ConversationRuntime
  eligible?: boolean
  isReadOnlySession?: boolean
}

// 语音消息生命周期管理：录音、自动停止、全局事件解绑、音轨清理与附件发送。

export function useVoiceRecorder({ isReadOnlySession, runtime, eligible = true }: Options): {
  recording: boolean
  start: () => void
  stop: () => Promise<void>
} {
  const auth = useStore($auth)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
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
  const finishRecordingActivity = useRef<(() => void) | null>(null)
  const finishUploadActivity = useRef<(() => void) | null>(null)

  useEffect(() => {
    const authCurrent = captureAuthScope()
    let disposed = false
    configRef.current = {}

    if (!authSessionId || !authCurrent) {
      return
    }

    void getSpiritAgentConfig()
      .then(c => {
        if (!disposed && authCurrent()) {
          configRef.current = { voice: c.voice }
        }
      })
      .catch((error: unknown) => {
        if (!disposed && authCurrent()) {
          // 读取失败时按默认时长上限录音。
          log.warn('voice-recorder', 'Could not load recording limit', error)
        }
      })

    return () => {
      disposed = true
    }
  }, [authSessionId])

  const cancelAutoStop = useCallback(() => {
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }, [])

  // 结束录音态；卸载后不再回写语音链路。
  const endRecording = useCallback(() => {
    setRecording(false)
    finishRecordingActivity.current?.()
    finishRecordingActivity.current = null

    if (recordingOwner === ownerToken.current) {
      recordingOwner = null
      conversationVoiceSink().setRecording(false)
    }
  }, [])

  // 中止录音并释放录制器与麦克风；operation 递增作废在途转写与等待中的 start。
  const abortRecording = useCallback(() => {
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
    finishUploadActivity.current?.()
    finishUploadActivity.current = null
  }, [cancelAutoStop, endRecording])

  const stop = useCallback(async () => {
    const auth = $auth.get()
    const authCurrent = captureAuthScope()

    if (auth.kind !== 'authenticated' || !authCurrent) {
      return
    }

    const operation = operationRef.current
    const recordingCurrent = (): boolean => authCurrent() && isCurrent() && operation === operationRef.current

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
    const finishUpload = beginConversationInputActivity('uploading')
    finishUploadActivity.current = finishUpload

    try {
      if (isReadOnlySession) {
        return
      }

      const sessionId = await ensureChatSession(runtime)

      if (!recordingCurrent()) {
        return
      }

      const dataUrl = await blobToDataUrl(blob)

      if (!recordingCurrent()) {
        return
      }

      const uploaded = await mediaApi().media.uploadAudio({
        authSessionId: auth.snapshot.sessionId,
        dataUrl,
        filename: `voice.${getAudioExtensionForMime(blob.type)}`,
        sessionId
      })

      if (!recordingCurrent()) {
        return
      }

      const attachments = [{ type: 'audio' as const, url: uploaded.url }]
      pushUserMessage('', attachments)
      pushPendingPrompt({ text: '', attachments })
      schedulePendingFlush()
    } catch (err) {
      log.warn('voice-recorder', 'Voice message send failed:', err)

      if (recordingCurrent()) {
        presentationPorts().setSpriteState('idle', { force: true })
        markAssistantTerminal({
          error: isMediaBusyError(err)
            ? getStrings().chat.voiceInput.busy
            : errorMessage(err, getStrings().chat.sendFailed)
        })
      }
    } finally {
      finishUpload()

      if (finishUploadActivity.current === finishUpload) {
        finishUploadActivity.current = null
      }
    }
  }, [
    isReadOnlySession,
    runtime,
    markAssistantTerminal,
    pushPendingPrompt,
    pushUserMessage,
    schedulePendingFlush,
    isCurrent,
    cancelAutoStop,
    endRecording
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
    finishRecordingActivity.current = beginConversationInputActivity('recording')
    const operation = ++operationRef.current
    conversationVoiceSink().setRecording(true)
    let pending: Promise<void> | null = null
    pending = (async () => {
      let stream: MediaStream | null = null

      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: CHAT_AUDIO_CONSTRAINTS })

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
  }, [isReadOnlySession, markAssistantTerminal, isCurrent, endRecording])

  useEffect(() => {
    if (eligible) {
      return
    }

    abortRecording()
  }, [eligible, abortRecording])

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
      abortRecording()
    }
  }, [runtime, abortRecording])

  return { recording, start, stop }
}
