import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { log } from '@/shared/lib/log'
import { gatewayApi, mediaApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'

import { useCallConnection } from '../../../../shared/realtime-call-connection'

function prepareOutput(): Promise<unknown> {
  return mediaApi().media.prepareOutput()
}

function startupError(error: unknown): string {
  log.warn('voice-call', 'Call startup failed', error)

  return '无法开始通话，请检查麦克风权限和连接'
}

function cleanupError(error: unknown): void {
  log.warn('voice-call', 'Audio cleanup failed', error)
}

export function useRealtimeCall({ sessionId, eligible }: { sessionId: string | null; eligible: boolean }) {
  const auth = useStore($auth)
  const authId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  const call = useCallConnection({
    scope: eligible && authId && sessionId ? `${authId}:${sessionId}` : null,
    prepareOutput,
    startupError,
    cleanupError,
    transport: ({ signal, receive, closed }) => {
      const callKey = crypto.randomUUID()

      return {
        connect: () => {
          if (!authId || !sessionId) {
            return Promise.reject(new Error('Voice connection is not authorized'))
          }

          const dispose = gatewayApi().onVoiceEvent(event => {
            if (event.kind === 'busy' || event.callKey !== callKey) {
              return
            }

            if (event.kind === 'message') {
              receive(event.data)
            } else {
              closed(event.failed)
            }
          })

          signal.addEventListener('abort', dispose, { once: true })

          return gatewayApi().voiceConnect({ authSessionId: authId, sessionId, callKey })
        },
        send: data => gatewayApi().voiceSend(callKey, data),
        close: () => gatewayApi().voiceClose(callKey)
      }
    }
  })

  const { stop } = call

  useEffect(
    () =>
      $auth.listen(value => {
        if (value.kind !== 'authenticated' || value.snapshot.sessionId !== authId) {
          stop()
        }
      }),
    [authId, stop]
  )

  return call
}
