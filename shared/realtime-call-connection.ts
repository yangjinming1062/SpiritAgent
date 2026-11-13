import { useCallback, useEffect, useRef, useState } from 'react'

import { RealtimeAudioBridge } from './realtime-audio'
import { type CallPart, type CallTransport, RealtimeCallController, type RealtimeCallSnapshot } from './realtime-call'

interface CallConnectionOptions {
  scope: string | null
  transport: (handlers: {
    signal: AbortSignal
    receive: RealtimeCallController['receive']
    closed: (failed?: boolean) => void
  }) => CallTransport
  prepareOutput?: () => Promise<unknown>
  startupError: (error: unknown) => string
  cleanupError: (error: unknown) => void
}

const IDLE: RealtimeCallSnapshot = {
  state: 'idle',
  muted: false,
  connectedAt: null,
  turnSeq: 0,
  playbackEpoch: 0,
  error: null,
  transcript: '',
  assistant: '',
  part: null
}

export function useCallConnection({
  scope,
  transport,
  prepareOutput,
  startupError,
  cleanupError
}: CallConnectionOptions) {
  const active = useRef<{ controller: RealtimeCallController; audio: RealtimeAudioBridge } | null>(null)
  const [snapshot, setSnapshot] = useState(IDLE)
  const stop = useCallback((): void => {
    active.current?.controller.stop()
  }, [])

  const start = useCallback(async (): Promise<void> => {
    if (!scope || document.hidden || active.current) {
      return
    }
    const lifetime = new AbortController()
    const controller = new RealtimeCallController({
      transport: transport({
        signal: lifetime.signal,
        receive: data => controller.receive(data),
        closed: failed => (failed ? controller.fail('通话连接已中断') : controller.transportClosed())
      }),
      onSnapshot: value => {
        if (active.current?.controller === controller) {
          setSnapshot(value)
        }
      },
      onAudioFrame: (frame, meta) => audio.enqueueAudio(frame, meta),
      onClear: () => audio.clearOutput(),
      onEnded: () => {
        lifetime.abort()
        void audio.stop().catch(cleanupError)
        if (active.current?.controller === controller) {
          active.current = null
        }
      }
    })
    const audio = new RealtimeAudioBridge(controller)
    active.current = { controller, audio }
    try {
      if (prepareOutput) {
        await controller.start()
        if (!lifetime.signal.aborted) {
          await prepareOutput()
        }
        if (!lifetime.signal.aborted) {
          await audio.start()
        }
      } else {
        // Safari 要求 AudioContext.resume() 在用户手势内开始。
        await Promise.all([controller.start(), audio.start()])
      }
      if (!lifetime.signal.aborted) {
        controller.audioReady()
      }
    } catch (error) {
      if (!lifetime.signal.aborted) {
        controller.fail(startupError(error))
      }
    }
  }, [scope, transport, prepareOutput, startupError, cleanupError])

  useEffect(() => {
    const hidden = (): void => {
      if (document.hidden) {
        stop()
      }
    }
    document.addEventListener('visibilitychange', hidden)
    window.addEventListener('pagehide', stop)
    window.addEventListener('beforeunload', stop)
    return () => {
      document.removeEventListener('visibilitychange', hidden)
      window.removeEventListener('pagehide', stop)
      window.removeEventListener('beforeunload', stop)
      stop()
    }
  }, [scope, stop])

  const hangup = useCallback((): void => {
    const call = active.current
    if (call) {
      call.controller.stop(true)
      void call.audio.stop().catch(cleanupError)
    }
  }, [cleanupError])
  const toggleMute = useCallback((): void => {
    const call = active.current
    if (call) {
      call.audio.mute(!call.controller.muted)
    }
  }, [])
  const receivedPart = useCallback((part: CallPart) => active.current?.controller.receivedPart(part), [])
  const levels = useCallback(() => active.current?.audio.levels() ?? { microphone: 0, playback: 0 }, [])

  return { snapshot, levels, start, stop, hangup, toggleMute, receivedPart }
}
