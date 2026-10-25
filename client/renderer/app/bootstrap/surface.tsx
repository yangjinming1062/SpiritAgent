import '../../styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ProxyGatewayPump } from '@/app/runtime/proxy-runtime'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { hydratePersona, hydratePortrait } from '@/modules/character'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { NotificationStack } from '@/shared/components/notifications'
import { initGlassBudgetGuard } from '@/shared/lib/apply-no-blur'
import { createGatewayView } from '@/shared/lib/gateway-view'
import { CaptureWindowIdContext, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { installUpdateBridge } from '@/shared/lib/update-bridge'
import { ConfirmHost } from '@/shared/panel'
import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { setSurfaceRole } from '@/shared/store/surfaces'

import { AccountScopedRoot } from './account-scoped'
import { initRenderer } from './init-renderer'

function SurfaceAuthBootstrap(): null {
  const auth = useStore($auth)
  const accountId = auth.kind === 'authenticated' ? auth.snapshot.accountId : null

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    void hydratePersona()
    void hydratePortrait()

    const onFocus = (): void => {
      void hydratePersona({ silent: true })
      void hydratePortrait()
    }

    window.addEventListener('focus', onFocus)

    return () => {
      window.removeEventListener('focus', onFocus)
    }
  }, [accountId, auth.kind])

  return null
}

function SurfaceLifecycle({ role }: { role: 'main' | 'desktop' }): React.JSX.Element {
  useAccountLifecycle()
  useEffect(() => initGlassBudgetGuard(), [])

  return role === 'desktop' ? <DesktopLifecycle /> : <SurfaceAuthBootstrap />
}

function DesktopLifecycle(): null {
  const auth = useStore($auth)
  useWindowMouseCapture(1, {
    releaseDelayMs: 0,
    setIgnoreMouseEvents: payload => shellApi().presentation.setIgnoreMouseEvents(payload),
    subscribePointerPosition: desktopPlatform() === 'darwin' ? shellApi().presentation.onPointerPosition : undefined
  })

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    const heartbeat = (): void => {
      void shellApi()
        .presentation.heartbeat()
        .catch(error => log.warn('desktop', 'heartbeat failed', error))
    }

    void shellApi()
      .presentation.reportReady()
      .catch(error => log.warn('desktop', 'ready failed', error))
    heartbeat()
    const timer = window.setInterval(heartbeat, 1000)

    return () => window.clearInterval(timer)
  }, [auth.kind])

  return null
}

export function bootstrapSurface(role: 'main' | 'desktop', RootComponent: React.ComponentType): void {
  const label = role === 'main' ? 'main-root' : 'desktop'

  if (role === 'desktop') {
    document.documentElement.dataset.role = role
  }

  setSurfaceRole(role)
  initRenderer()
  const offUpdateBridge = installUpdateBridge()

  if (import.meta.hot) {
    import.meta.hot.dispose(offUpdateBridge)
  }

  setPrimaryGateway(createGatewayView())

  const container = document.getElementById('root')

  if (!container) {
    throw new Error(`${label}: missing #root element`)
  }

  // 未指定窗口 ID 的交互区域（含 Portal 弹层）登记到 1，与根组件的捕获循环一致。
  createRoot(container).render(
    <StrictMode>
      <CaptureWindowIdContext value={1}>
        <ErrorBoundary label={label}>
          <HapticsProvider>
            <HashRouter>
              <SurfaceLifecycle role={role} />
              <ProxyGatewayPump />
              <AccountScopedRoot RootComponent={RootComponent} />
              <NotificationStack />
              <ConfirmHost />
            </HashRouter>
          </HapticsProvider>
        </ErrorBoundary>
      </CaptureWindowIdContext>
    </StrictMode>
  )
}
