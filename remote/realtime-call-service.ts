import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText, type RemoteApi } from './api'
import { RealtimeAudioBridge } from './realtime-audio'
import { type CallPart, RealtimeCallController, type RealtimeCallSnapshot } from './realtime-call'

export interface RemoteRealtimeCallControls {
  muted: boolean
  snapshot: RealtimeCallSnapshot
  seconds: number
  start: () => Promise<void>
  stop: () => void
  toggleMute: () => void
  receivedPart: (part: CallPart) => void
}

interface ActiveCall {
  controller: RealtimeCallController
  audio: RealtimeAudioBridge
}

const IDLE: RealtimeCallSnapshot = {
  state: 'idle',
  muted: false,
  turnSeq: 0,
  playbackEpoch: 0,
  error: null,
  transcript: '',
  assistant: '',
  part: null
}

export function useRemoteRealtimeCall({
  api,
  sessionId,
  eligible
}: {
  api: RemoteApi
  sessionId: string | null
  eligible: boolean
}): RemoteRealtimeCallControls {
  const active = useRef<ActiveCall | null>(null)
  const [snapshot, setSnapshot] = useState(IDLE)
  const [seconds, setSeconds] = useState(0)

  const stop = useCallback((): void => {
    active.current?.controller.stop()
  }, [])

  const start = useCallback(async (): Promise<void> => {
    if (!sessionId || !eligible || active.current) {
      return
    }
    const startAbort = new AbortController()
    let currentSocket: WebSocket | null = null
    const controller = new RealtimeCallController({
      transport: {
        connect: async () => {
          const url = await api.realtimeUrl(sessionId, { signal: startAbort.signal, timeoutMs: 15_000 })
          if (startAbort.signal.aborted) {
            throw new DOMException('通话启动已取消', 'AbortError')
          }
          await new Promise<void>((resolve, reject) => {
            const next = new WebSocket(url)
            currentSocket = next
            next.binaryType = 'arraybuffer'
            next.onopen = () => resolve()
            next.onerror = () => reject(new Error('通话连接失败'))
            next.onmessage = event => controller.receive(event.data)
            next.onclose = () => {
              reject(new Error('通话连接已关闭'))
              controller.fail('通话连接已中断')
            }
          })
        },
        send: data => {
          if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
            return Promise.reject(new Error('通话连接尚未打开'))
          }
          currentSocket.send(typeof data === 'string' ? data : (data.slice().buffer as ArrayBuffer))
          return Promise.resolve()
        },
        close: () => {
          currentSocket?.close()
          currentSocket = null
          return Promise.resolve()
        }
      },
      onSnapshot: value => {
        if (active.current?.controller === controller) {
          setSnapshot(value)
        }
      },
      onAudioFrame: (frame, meta) => audio.enqueueAudio(frame, meta),
      onClear: () => audio.clearOutput(),
      onEnded: () => {
        startAbort.abort()
        void audio.stop().catch(error => console.warn('通话音频清理失败', errorText(error)))
        if (active.current?.controller === controller) {
          active.current = null
        }
      }
    })

    const audio = new RealtimeAudioBridge(controller)
    const current = { controller, audio }
    active.current = current
    setSeconds(0)

    try {
      await controller.start()
      if (active.current === current) {
        await audio.start()
      }
    } catch (failure) {
      controller.fail(errorText(failure) || '无法开始通话，请检查麦克风权限和连接')
    }
  }, [api, eligible, sessionId])

  const toggleMute = useCallback((): void => {
    const call = active.current
    if (call) {
      call.audio.mute(!call.controller.muted)
    }
  }, [])

  const receivedPart = useCallback((part: CallPart): void => {
    active.current?.controller.receivedPart(part)
  }, [])

  useEffect(() => stop, [eligible, sessionId, stop])

  const running = snapshot.state !== 'idle' && snapshot.state !== 'stopped' && snapshot.state !== 'error'
  useEffect(() => {
    if (!running) {
      return
    }
    const timer = setInterval(() => setSeconds(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [running])

  return { snapshot, muted: snapshot.muted, seconds, start, stop, toggleMute, receivedPart }
}
