import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { bindPresentation } from '@/app/bootstrap/bind-presentation'
import { SpriteBootstrap } from '@/app/bootstrap/sprite'
import { SpriteWindow } from '@/app/windows/sprite/sprite-window'
import { initCompanionPrefsSync } from '@/modules/character'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { initLocaleSync } from '@/shared/store/locale'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

installClipboardShim()
applyNoBlurIfNeeded()
bindPresentation()
initUiThemeSync()
initLocaleSync()
initCompanionPrefsSync()
hydrateSurfaces()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="sprite-root">
      <HapticsProvider>
        <SpriteBootstrap />
        <HashRouter>
          <SpriteWindow />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
