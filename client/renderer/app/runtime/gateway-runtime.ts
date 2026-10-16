import { conversationRuntimes, findConversationRuntime, syncSessionHistory } from '@/modules/conversation'
import { captureAuthScope } from '@/shared/lib/authed-api'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { log } from '@/shared/lib/log'
import { gatewayApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gateway, reportPrimaryGatewayState, setPrimaryGateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { DesktopGatewaySnapshot } from '@ipc/contracts'

import { handleGatewayEvent } from './gateway-event-router'
import { resumeHostHistory } from './host-history-recovery'

export function startGatewayView(
  authSessionId: string,
  presentationHost = false,
  onState?: (snapshot: DesktopGatewaySnapshot) => void
): (() => void) | undefined {
  const authScope = captureAuthScope()
  const current = $auth.get()

  if (!authScope || current.kind !== 'authenticated' || current.snapshot.sessionId !== authSessionId) {
    return
  }

  const desktop = gatewayApi()
  const gateway = new IpcGatewayProxy(presentationHost)
  let disposed = false
  let syncGeneration = 0
  let observedState: ConnectionState | undefined
  let observedSnapshot: DesktopGatewaySnapshot | undefined
  let receivedState = false
  let bootFailureNotified = false
  const isCurrent = (): boolean => !disposed && authScope() && $gateway.get() === gateway

  setPrimaryGateway(gateway)

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
        request: body => gateway.request('session.resume', { session_id: targetId, ...body })
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
            log.warn('gateway-view', 'Session recovery failed:', error)
          }
        })
    }
  }

  const applyState = (snapshot: DesktopGatewaySnapshot): void => {
    if (
      !isCurrent() ||
      (snapshot.state === observedSnapshot?.state &&
        snapshot.error === observedSnapshot.error &&
        snapshot.hasConnected === observedSnapshot.hasConnected &&
        snapshot.activeToolCount === observedSnapshot.activeToolCount)
    ) {
      return
    }

    observedSnapshot = snapshot
    const stateChanged = snapshot.state !== observedState
    observedState = snapshot.state
    reportPrimaryGatewayState(snapshot.state, snapshot.activeToolCount)
    onState?.(snapshot)

    if (!snapshot.hasConnected && snapshot.error && !bootFailureNotified) {
      bootFailureNotified = true
      notifyError(snapshot.error, getStrings().boot.errors.desktopBootFailed)
    }

    if (!stateChanged) {
      return
    }

    if (snapshot.state === 'open') {
      if (presentationHost) {
        void resumeHostHistory(gateway, isCurrent)
      } else {
        syncConversations()
      }
    } else if (snapshot.state === 'closed' || snapshot.state === 'error') {
      syncGeneration++

      for (const runtime of conversationRuntimes()) {
        runtime.abandonDisconnectedTurn()
      }
    }
  }

  const offState = desktop.onGatewayStateChanged(payload => {
    if (payload.authSessionId === authSessionId) {
      receivedState = true
      applyState(payload)
    }
  })

  const offEvent = desktop.onGatewayEvent(payload => {
    if (isCurrent() && payload.authSessionId === authSessionId) {
      handleGatewayEvent(payload.event)
    }
  })

  // 先订阅再读状态，读取期间的事件优先。
  void desktop
    .gatewayGetState(authSessionId)
    .then(state => {
      if (!receivedState) {
        applyState(state)
      }
    })
    .catch(error => {
      if (isCurrent()) {
        log.warn('gateway-view', 'Could not read gateway state:', error)
      }
    })

  return () => {
    disposed = true
    syncGeneration++
    offState()
    offEvent()

    if ($gateway.get() === gateway) {
      setPrimaryGateway(null)
    }
  }
}
