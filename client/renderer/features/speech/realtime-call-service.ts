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

export function useRealtimeCall({
  sessionId,
  eligible
}: {
  sessionId: string | null
  eligible: boolean
}): RealtimeCallControls {
  const active = useRef<ActiveCall | null>(null)
  const revision = useRef(0)
  const [snapshot, setSnapshot] = useState(IDLE)
  const [seconds, setSeconds] = useState(0)
  const auth = useStore($auth)
  const authId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const locked = useStore(presentationPorts().$screenLocked)

  const stop = useCallback(async (): Promise<void> => {
    revision.current++
    const current = active.current
    active.current = null

    if (current) {
      current.controller.stop()
      current.dispose()
      await current.audio.stop()
    }
  }, [])

  const start = useCallback(async (): Promise<void> => {
    if (!sessionId || !authId || !eligible || locked || active.current) {
      return
    }

    const attempt = ++revision.current
    const callKey = crypto.randomUUID()
    let audio: RealtimeAudioBridge

    let dispose = (): void => {}

    const controller = new RealtimeCallController({
      transport: {
        connect: () => gatewayApi().voiceConnect({ authSessionId: authId, sessionId, callKey }),
        send: data => gatewayApi().voiceSend(callKey, data),
        close: () => gatewayApi().voiceClose(callKey)
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
        void audio.stop().catch(error => log.warn('voice-call', 'Audio cleanup failed', error))

        if (active.current?.controller === controller) {
          active.current = null
        }
      }
    })

    audio = new RealtimeAudioBridge(controller)
    dispose = gatewayApi().onVoiceEvent(event => {
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
    const current = { controller, audio, dispose }
    active.current = current
    setSeconds(0)

    try {
      await controller.start()
      await mediaApi().media.prepareOutput()

      if (active.current !== current || revision.current !== attempt) {
        return
      }

      await audio.start()

      if (active.current !== current || revision.current !== attempt) {
        await audio.stop()
      }
    } catch (error) {
      log.warn('voice-call', 'Call startup failed', error)
      controller.fail('无法开始通话，请检查麦克风权限和连接')
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
        void stop()
      }
    })

    const unload = (): void => {
      void stop()
    }

    window.addEventListener('beforeunload', unload)

    if (!eligible || !sessionId || locked) {
      void stop()
    }

    return () => {
      disposeAuth()
      window.removeEventListener('beforeunload', unload)
      void stop()
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
