import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { initCompanionPrefsSync } from '@/companion'
import { useSurfaceSpriteLink } from '@/companion/hooks/use-surface-sprite-link'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { initLocaleSync } from '@/shared/store/locale'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

import { CompanionRoot } from './companion/root'

installClipboardShim()
applyNoBlurIfNeeded()
initUiThemeSync()
initLocaleSync()
initCompanionPrefsSync()
hydrateSurfaces()

function SpriteSurfaceLink(): null {
  useSurfaceSpriteLink()

  return null
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="sprite-root">
      <HapticsProvider>
        <SpriteSurfaceLink />
        <HashRouter>
          <CompanionRoot />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
