import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { conversationRuntimes, findConversationRuntime, syncSessionHistory } from '@/modules/conversation'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $gateway, reportPrimaryGatewayState, setPrimaryGateway } from '@/shared/store/gateway'
import type { SessionResumeResponse } from '@/shared/types/spiritagent'

import { handleGatewayEvent } from './gateway-event-router'
import { isDeviceCommandEvent } from './gateway-event-util'

// 代理入口共享宿主WS，只恢复历史；设备命令仍由宿主执行。
export function ProxyGatewayPump(): null {
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  useEffect(() => {
    const authScope = captureAuthScope()
    const current = $auth.get()

    if (!authScope || current.kind !== 'authenticated' || current.snapshot.sessionId !== sessionId) {
      return
    }

    const gateway = new IpcGatewayProxy()
    setPrimaryGateway(gateway)
    let disposed = false
    let syncGeneration = 0
    let observedState: Parameters<typeof reportPrimaryGatewayState>[0] | undefined
    let stateEvents = 0
    const isCurrent = (): boolean => !disposed && authScope() && $gateway.get() === gateway

    const syncConversations = (): void => {
      const generation = ++syncGeneration

      for (const runtime of conversationRuntimes()) {
        const targetId = runtime.$chatSessionId.get()

        if (!targetId || !runtime.isCurrent()) {
          continue
        }

        const revision = runtime.$runtimeRevision.get()
        void syncSessionHistory({
          sessionId: targetId,
          request: body => gateway.request<SessionResumeResponse>('session.resume', { session_id: targetId, ...body })
        })
          .then(result => {
            if (
              !isCurrent() ||
              generation !== syncGeneration ||
              gateway.connectionState !== 'open' ||
              findConversationRuntime(targetId) !== runtime
            ) {
              return
            }

            runtime.hydrateSyncedChatMessages(result.messages, result.info, revision)
          })
          .catch(error => {
            if (isCurrent() && generation === syncGeneration) {
              log.warn('proxy-gateway', 'Session recovery failed:', error)
            }
          })
      }
    }

    const applyState = (state: Parameters<typeof reportPrimaryGatewayState>[0]): void => {
      if (!isCurrent() || state === observedState) {
        return
      }

      observedState = state
      reportPrimaryGatewayState(state)

      if (state === 'open') {
        syncConversations()
      } else if (state === 'closed' || state === 'error') {
        syncGeneration++

        for (const runtime of conversationRuntimes()) {
          runtime.abandonDisconnectedTurn()
        }
      }
    }

    const desktop = window.spiritagent

    if (!desktop) {
      return
    }

    const initialEvents = stateEvents
    void desktop
      .gatewayGetState?.()
      .then(state => {
        if (state && stateEvents === initialEvents) {
          applyState(state)
        }
      })
      .catch(error => {
        if (isCurrent()) {
          log.warn('proxy-gateway', 'Could not read gateway state:', error)
        }
      })

    const offState = desktop.onGatewayStateChanged?.(payload => {
      if (payload?.state) {
        stateEvents++
        applyState(payload.state)
      }
    })

    const offEvent = desktop.onGatewayEvent?.(payload => {
      if (isCurrent() && payload?.event && !isDeviceCommandEvent(payload.event.type)) {
        handleGatewayEvent(payload.event)
      }
    })

    return () => {
      disposed = true
      syncGeneration++
      offState?.()
      offEvent?.()
    }
  }, [sessionId])

  return null
}
