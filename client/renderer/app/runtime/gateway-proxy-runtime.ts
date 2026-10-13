import { conversationRuntimes, findConversationRuntime, syncSessionHistory } from '@/modules/conversation'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { log } from '@/shared/lib/log'
import { gatewayApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gateway, reportPrimaryGatewayState, setPrimaryGateway } from '@/shared/store/gateway'
import type { SessionResumeResponse } from '@protocol'

import { handleGatewayEvent } from './gateway-event-router'
import { isDeviceCommandEvent } from './gateway-event-util'

export function createProxyGatewayRuntime(authSessionId: string) {
  let started = false
  let disposed = false
  let cleanup: (() => void) | undefined

  function start(): void {
    if (started || disposed) {
      return
    }

    started = true
    const authScope = captureAuthScope()
    const current = $auth.get()

    if (!authScope || current.kind !== 'authenticated' || current.snapshot.sessionId !== authSessionId) {
      return
    }

    const desktop = gatewayApi()

    if (!desktop) {
      return
    }

    const gateway = new IpcGatewayProxy()
    setPrimaryGateway(gateway)
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

        const snapshot = runtime.captureHistorySync()
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

            runtime.applyRemoteSnapshot(result, snapshot)

            void runtime.recoverUnconfirmedSubmission()

            if (!runtime.$chatTurnInFlight.get()) {
              runtime.submitPendingBatch()
            }
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

    const initialEvents = stateEvents

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

    cleanup = () => {
      disposed = true
      syncGeneration++
      offState?.()
      offEvent?.()

      if ($gateway.get() === gateway) {
        setPrimaryGateway(null)
      }
    }

    // 先订阅再读快照；读取期间的新状态优先，旧实例释放时不清除后来装配的网关。
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
  }

  return {
    start,
    dispose: () => {
      disposed = true
      cleanup?.()
      cleanup = undefined
    }
  }
}
