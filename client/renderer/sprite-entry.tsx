import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { AccountScopedRoot } from '@/app/bootstrap/account-scoped'
import { initRenderer } from '@/app/bootstrap/init-renderer'
import { SpriteBootstrap } from '@/app/bootstrap/sprite'
import { SpriteWindow } from '@/app/windows/sprite/sprite-window'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { setSurfaceRole } from '@/shared/store/surfaces'

setSurfaceRole('sprite')
initRenderer()

const container = document.getElementById('root')

if (!container) {
  throw new Error('sprite-root: missing #root element')
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary label="sprite-root">
      <HapticsProvider>
        <SpriteBootstrap />
        <HashRouter>
          <AccountScopedRoot RootComponent={SpriteWindow} />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
