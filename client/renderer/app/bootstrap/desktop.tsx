import '../../styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ProxyGatewayPump } from '@/app/runtime/proxy-runtime'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { useDesktopCompanionActivityPublisher } from '@/app/workflows/desktop-companion-activity'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { NotificationStack } from '@/shared/components/notifications'
import { initGlassBudgetGuard } from '@/shared/lib/apply-no-blur'
import { CaptureWindowIdContext, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { log } from '@/shared/lib/log'
import { installUpdateBridge } from '@/shared/lib/update-bridge'
import { $auth } from '@/shared/store/auth'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { setSurfaceRole } from '@/shared/store/surfaces'

import { AccountScopedRoot } from './account-scoped'
import { initRenderer } from './init-renderer'

function DesktopLifecycle(): null {
  useDesktopCompanionActivityPublisher()
  useAccountLifecycle()
  const auth = useStore($auth)

  useEffect(() => initGlassBudgetGuard(), [])
  useWindowMouseCapture(1, {
    setIgnoreMouseEvents: payload => window.spiritagent.presentation.setIgnoreMouseEvents(payload)
  })

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    const heartbeat = (): void => {
      void window.spiritagent.presentation.heartbeat().catch(error => log.warn('desktop', 'heartbeat failed', error))
    }

    void window.spiritagent.presentation.reportReady().catch(error => log.warn('desktop', 'ready failed', error))
    heartbeat()
    const timer = window.setInterval(heartbeat, 1000)

    return () => window.clearInterval(timer)
  }, [auth.kind])

  return null
}

export function bootstrapDesktop(RootComponent: React.ComponentType): void {
  setSurfaceRole('desktop')
  initRenderer()
  setPrimaryGateway(new IpcGatewayProxy())
  const offUpdate = installUpdateBridge()

  if (import.meta.hot) {
    import.meta.hot.dispose(offUpdate)
  }

  const root = document.getElementById('root')

  if (!root) {
    throw new Error('desktop: missing root element')
  }

  createRoot(root).render(
    <StrictMode>
      <CaptureWindowIdContext value={1}>
        <ErrorBoundary label="desktop">
          <HapticsProvider>
            <HashRouter>
              <DesktopLifecycle />
              <ProxyGatewayPump />
              <AccountScopedRoot RootComponent={RootComponent} />
              <NotificationStack />
            </HashRouter>
          </HapticsProvider>
        </ErrorBoundary>
      </CaptureWindowIdContext>
    </StrictMode>
  )
}
