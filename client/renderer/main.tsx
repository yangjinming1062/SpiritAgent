import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { bindPresentation } from '@/app/bootstrap/bind-presentation'
import { SpriteBootstrap } from '@/app/bootstrap/sprite'
import { SpriteWindow } from '@/app/windows/sprite/sprite-window'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { installUpdateBridge } from '@/shared/lib/update-bridge'

installClipboardShim()
applyNoBlurIfNeeded()
bindPresentation()
const offUpdateBridge = installUpdateBridge()

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    offUpdateBridge()
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="root">
      <HapticsProvider>
        <SpriteBootstrap />
        <HashRouter>
          <SpriteWindow />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
