import '../../styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ProxyGatewayPump } from '@/app/runtime/proxy-runtime'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { hydratePersona, hydratePortrait, initCompanionPrefsSync } from '@/modules/character'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { NotificationStack } from '@/shared/components/notifications'
import { applyNoBlurIfNeeded, initGlassBudgetGuard } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { installUpdateBridge } from '@/shared/lib/update-bridge'
import { $auth } from '@/shared/store/auth'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { initLocaleSync } from '@/shared/store/locale'
import { hydrateSurfaces, setSurfaceRole } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

import { bindPresentation } from './bind-presentation'

function SurfaceAuthBootstrap(): null {
  useAccountLifecycle()
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

function SurfaceGlassBudgetGuard(): null {
  useEffect(() => initGlassBudgetGuard(), [])

  return null
}

// 完整入口的根组件以窗口 ID 1 捕获鼠标；toast 登记到同一 ID，落在外壳之外也能点击。
function SurfaceNotificationStack(): React.JSX.Element {
  const regionRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('notification-stack', regionRef, undefined, undefined, 1)

  return <NotificationStack regionRef={regionRef} />
}

function AccountScopedSurface({ RootComponent }: { RootComponent: React.ComponentType }): React.JSX.Element {
  const auth = useStore($auth)
  const key = auth.kind === 'authenticated' ? auth.snapshot.accountId : auth.kind

  return <RootComponent key={key} />
}

export function bootstrapSurface(label: string, RootComponent: React.ComponentType): void {
  const isLiving = label.includes('living')

  if (isLiving) {
    setSurfaceRole('living')
  } else if (label.includes('workbench')) {
    setSurfaceRole('workbench')
  }

  installClipboardShim()
  applyNoBlurIfNeeded()
  bindPresentation()
  initUiThemeSync()
  initLocaleSync()
  initCompanionPrefsSync()
  hydrateSurfaces()

  if (isLiving) {
    // 更新状态唯一消费方（设置页 about）在生活空间。
    const offUpdateBridge = installUpdateBridge()

    if (import.meta.hot) {
      import.meta.hot.dispose(offUpdateBridge)
    }
  }

  setPrimaryGateway(new IpcGatewayProxy())

  const container = document.getElementById('root')

  if (!container) {
    throw new Error(`${label}: missing #root element`)
  }

  createRoot(container).render(
    <StrictMode>
      <ErrorBoundary label={label}>
        <HapticsProvider>
          <HashRouter>
            <SurfaceGlassBudgetGuard />
            <SurfaceAuthBootstrap />
            <ProxyGatewayPump />
            <AccountScopedSurface RootComponent={RootComponent} />
            <SurfaceNotificationStack />
          </HashRouter>
        </HapticsProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
