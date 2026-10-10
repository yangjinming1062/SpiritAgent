import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'
import type React from 'react'

import { PresentationModeButton } from '@/app/components/presentation-mode-switch'
import { CompanionMenu } from '@/app/components/surface-companion/companion-menu'
import { SurfaceCompanion } from '@/app/components/surface-companion/surface-companion'
import { LivingRail } from '@/app/features/living/living-rail'
import { LivingStage } from '@/app/features/living/living-stage'
import { $livingView } from '@/app/features/living/living-store'
import styles from '@/app/features/living/living.module.css'
import { SceneBackdrop } from '@/app/features/living/scene-backdrop'
import { SpriteStatusBadge } from '@/modules/character'
import { MediaViewerOverlay } from '@/modules/media'
import { hydrateDiaryUnread } from '@/modules/memory'
import { hydratePostsUnread } from '@/modules/posts'
import { hydrateScene } from '@/modules/scene'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared'
import { ArrowRight, Home } from '@/shared/lib/icons'
import { WindowControls } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $surfaceOpenVisible, $surfaceScreenLocked, requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

export function LivingRoot(): React.JSX.Element {
  useWindowMouseCapture(1, { setIgnoreMouseEvents: window.spiritagent?.surface?.setIgnoreMouseEvents })
  const shellRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('living-shell', shellRef, undefined, undefined, 1)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const livingView = useStore($livingView)
  const dict = useStrings()
  const t = dict.living

  useEffect(() => {
    document.title = `${dict.brand.name} · ${t.title}`
  }, [dict.brand.name, t.title])

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void hydrateScene()
    }
  }, [auth.kind, gatewayState])

  useEffect(() => {
    if (!sessionId) {
      return
    }

    const refresh = (): void => {
      void hydratePostsUnread()
      void hydrateDiaryUnread()
    }

    const onVisible = (): void => {
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }

    refresh()

    const stopGateway = $gatewayState.listen(state => {
      if (state === 'open') {
        refresh()
      }
    })

    const stopVisibility = $surfaceOpenVisible.listen(visible => {
      if (visible) {
        refresh()
      }
    })

    const stopLock = $surfaceScreenLocked.listen(locked => {
      if (!locked) {
        refresh()
      }
    })

    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      stopGateway()
      stopVisibility()
      stopLock()
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [sessionId])

  useEffect(() => {
    const root = document.documentElement
    root.dataset.livingView = livingView

    return () => {
      delete root.dataset.livingView
    }
  }, [livingView])

  return (
    <div className={styles.windowFrame}>
      <SurfaceCompanion surface="living" />
      <div className={styles.contentFrame}>
        <MediaViewerOverlay containerRef={shellRef} windowId={1} />
        <SceneBackdrop />
        <div aria-hidden="true" className={styles.glassPlate} />
        <div className={styles.shell} data-surface="living" ref={shellRef}>
          <header
            className={styles.titlebar}
            onDoubleClick={() => {
              void window.spiritagent?.surface?.maximize?.()
            }}
          >
            <div className={styles.titleArea}>
              <Home className={styles.titleIcon} size={18} />
              <h1 className={styles.title}>{t.title}</h1>
              <SpriteStatusBadge />
            </div>
            <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
              <PresentationModeButton className={styles.workbenchButton} />
              <CompanionMenu surface="living" />
              <button
                className={styles.workbenchButton}
                onClick={() => {
                  void requestOpenSurface('workbench')
                }}
                type="button"
              >
                <span>{t.goToWorkbench}</span>
                <ArrowRight size={13} />
              </button>
              <WindowControls />
            </div>
          </header>

          <div className={styles.body}>
            <LivingRail />
            <main className={styles.stage}>
              <LivingStage />
            </main>
          </div>
        </div>
      </div>
    </div>
  )
}
