import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText, type RemoteApi } from './api'
import { RealtimeAudioBridge } from './realtime-audio'
import { type CallPart, RealtimeCallController, type RealtimeCallSnapshot } from './realtime-call'

export interface RemoteRealtimeCallControls {
  muted: boolean
  snapshot: RealtimeCallSnapshot
  seconds: number
  start: () => Promise<void>
  stop: () => Promise<void>
  toggleMute: () => void
  receivedPart: (part: CallPart) => void
}

interface ActiveCall {
  controller: RealtimeCallController
  audio: RealtimeAudioBridge
  dispose: () => void
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
  const revision = useRef(0)
  const socket = useRef<WebSocket | null>(null)
  const [snapshot, setSnapshot] = useState(IDLE)
  const [seconds, setSeconds] = useState(0)

  const stop = useCallback(async (): Promise<void> => {
    revision.current++
    const current = active.current
    active.current = null
    socket.current = null
    if (current) {
      current.controller.stop()
      current.dispose()
      await current.audio.stop()
    }
  }, [])

  const start = useCallback(async (): Promise<void> => {
    if (!sessionId || !eligible || active.current) {
      return
    }
    const attempt = ++revision.current
    let audio: RealtimeAudioBridge
    let currentSocket: WebSocket | null = null
    let dispose = (): void => {}
    const controller = new RealtimeCallController({
      transport: {
        connect: async () => {
          const url = await api.realtimeUrl(sessionId)
          await new Promise<void>((resolve, reject) => {
            const next = new WebSocket(url)
            currentSocket = next
            socket.current = next
            next.binaryType = 'arraybuffer'
            next.onopen = () => resolve()
            next.onerror = () => reject(new Error('通话连接失败'))
            next.onmessage = event => controller.receive(event.data)
            next.onclose = () => {
              if (!controller.snapshot().state || controller.snapshot().state === 'connecting') {
                reject(new Error('通话连接已关闭'))
              } else if (controller.snapshot().state !== 'stopped' && controller.snapshot().state !== 'error') {
                controller.fail('通话连接已中断')
              }
            }
          })
        },
        send: data => {
          if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
            return Promise.reject(new Error('通话连接尚未打开'))
          }
          currentSocket.send(typeof data === 'string' ? data : (data.buffer as ArrayBuffer))
          return Promise.resolve()
        },
        close: () => {
          currentSocket?.close()
          currentSocket = null
          socket.current = null
          return Promise.resolve()
        }
      },
      onSnapshot: value => {
        if (revision.current === attempt) {
          setSnapshot(value)
        }
      },
      onAudioFrame: (frame, meta) => audio.enqueueAudio(frame, meta),
      onClear: () => audio?.clearOutput(),
      onEnded: () => {
        dispose()
        void audio.stop()
        if (active.current?.controller === controller) {
          active.current = null
        }
      }
    })

    audio = new RealtimeAudioBridge(controller)
    const current: ActiveCall = { controller, audio, dispose }
    active.current = current
    setSeconds(0)

    try {
      await controller.start()
      if (active.current !== current || revision.current !== attempt) {
        return
      }
      await audio.start()
      if (active.current !== current || revision.current !== attempt) {
        await audio.stop()
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

  useEffect(() => {
    if (!eligible || !sessionId) {
      void stop()
    }
    return () => {
      void stop()
    }
  }, [eligible, sessionId, stop])

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
