import './styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { initCompanionPrefsSync } from '@/companion'
import { handleCompanionEvent } from '@/companion/events'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait } from '@/companion/portrait-store'
import { useAuthBridge } from '@/companion/use-auth-bridge'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { IpcGatewayProxy } from '@/shared/lib/ipc-gateway-proxy'
import { $auth } from '@/shared/store/auth'
import { reportPrimaryGatewayState, setPrimaryGateway } from '@/shared/store/gateway'
import { initLocaleSync } from '@/shared/store/locale'
import { hydrateSurfaces, setSurfaceRole } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

function SurfaceGatewayBootstrap(): null {
  useEffect(() => {
    const desktop = window.spiritagent

    if (!desktop) {
      return
    }

    void desktop.gatewayGetState?.().then(st => {
      if (st) {
        reportPrimaryGatewayState(st)
      }
    })

    const offState = desktop.onGatewayStateChanged?.(payload => {
      if (payload?.state) {
        reportPrimaryGatewayState(payload.state)
      }
    })

    const offEvent = desktop.onGatewayEvent?.(payload => {
      if (payload?.event && payload.event.type !== 'tool.call') {
        handleCompanionEvent(payload.event as unknown as GatewayEvent)
      }
    })

    return () => {
      offState?.()
      offEvent?.()
    }
  }, [])

  return null
}

function SurfaceAuthBootstrap(): null {
  useAuthBridge()
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
            <SurfaceGatewayBootstrap />
            <RootComponent />
          </HashRouter>
        </HapticsProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
