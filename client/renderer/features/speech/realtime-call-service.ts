import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { log } from '@/shared/lib/log'
import { gatewayApi, mediaApi } from '@/shared/platform/desktop'
import { presentationPorts } from '@/shared/presentation-ports'
import { $auth } from '@/shared/store/auth'

import { RealtimeAudioBridge } from './realtime-audio'
import { type CallPart, RealtimeCallController, type RealtimeCallSnapshot } from './realtime-call'

export interface RealtimeCallControls {
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

export function useRealtimeCall({
  sessionId,
  eligible
}: {
  sessionId: string | null
  eligible: boolean
}): RealtimeCallControls {
  const active = useRef<ActiveCall | null>(null)
  const [snapshot, setSnapshot] = useState(IDLE)
  const [seconds, setSeconds] = useState(0)
  const auth = useStore($auth)
  const authId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const locked = useStore(presentationPorts().$screenLocked)

  const stop = useCallback((): void => {
    active.current?.controller.stop()
  }, [])

  const start = useCallback(async (): Promise<void> => {
    if (!sessionId || !authId || !eligible || locked || active.current) {
      return
    }

    const callKey = crypto.randomUUID()

    const controller = new RealtimeCallController({
      transport: {
        connect: () => gatewayApi().voiceConnect({ authSessionId: authId, sessionId, callKey }),
        send: data => gatewayApi().voiceSend(callKey, data),
        close: () => gatewayApi().voiceClose(callKey)
      },
      onSnapshot: value => {
        if (active.current?.controller === controller) {
          setSnapshot(value)
        }
      },
      onAudioFrame: (frame, meta) => audio.enqueueAudio(frame, meta),
      onClear: () => audio.clearOutput(),
      onEnded: () => {
        dispose()
        void audio.stop().catch(error => log.warn('voice-call', 'Audio cleanup failed', error))

        if (active.current?.controller === controller) {
          active.current = null
        }
      }
    })

    const audio = new RealtimeAudioBridge(controller)

    const dispose = gatewayApi().onVoiceEvent(event => {
      if (event.kind === 'busy' || event.callKey !== callKey) {
        return
      }

      if (event.kind === 'message') {
        controller.receive(event.data)
      } else if (event.failed) {
        controller.fail('通话连接已中断')
      } else {
        controller.stop()
      }
    })

    const current = { controller, audio }
    active.current = current
    setSeconds(0)

    try {
      await controller.start()

      if (active.current !== current) {
        return
      }

      await mediaApi().media.prepareOutput()

      if (active.current === current) {
        await audio.start()
      }
    } catch (error) {
      if (controller.snapshot().state !== 'stopped') {
        log.warn('voice-call', 'Call startup failed', error)
        controller.fail('无法开始通话，请检查麦克风权限和连接')
      }
    }
  }, [authId, eligible, locked, sessionId])

  const toggleMute = useCallback((): void => {
    const call = active.current

    if (call) {
      call.audio.mute(!call.controller.muted)
    }
  }, [])

  const receivedPart = useCallback((part: CallPart): void => active.current?.controller.receivedPart(part), [])
  useEffect(() => {
    const disposeAuth = $auth.listen(value => {
      if (value.kind !== 'authenticated' || value.snapshot.sessionId !== authId) {
        stop()
      }
    })

    window.addEventListener('beforeunload', stop)

    if (!eligible || !sessionId || locked) {
      stop()
    }

    return () => {
      disposeAuth()
      window.removeEventListener('beforeunload', stop)
      stop()
    }
  }, [authId, eligible, locked, sessionId, stop])
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
