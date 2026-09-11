import '../../styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ProxyGatewayPump } from '@/app/runtime/proxy-runtime'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { hydratePersona, hydratePortrait, initCompanionPrefsSync } from '@/modules/character'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { $auth } from '@/shared/store/auth'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { initLocaleSync } from '@/shared/store/locale'
import { hydrateSurfaces, setSurfaceRole } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

import { bindPresentation } from './bind-presentation'

function SurfaceAuthBootstrap(): null {
  useAccountLifecycle()
  const auth = useStore($auth)

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
  }, [auth.kind])

  return null
}

export function bootstrapSurface(label: string, RootComponent: React.ComponentType): void {
  if (label.includes('living')) {
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
            <SurfaceAuthBootstrap />
            <ProxyGatewayPump />
            <RootComponent />
          </HashRouter>
        </HapticsProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
