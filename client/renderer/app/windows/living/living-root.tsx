import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'
import type React from 'react'

import { CompanionMenu } from '@/app/components/surface-companion/companion-menu'
import { SurfaceCompanion } from '@/app/components/surface-companion/surface-companion'
import { SpriteStatusBadge } from '@/modules/character'
import { MediaViewerOverlay } from '@/modules/media'
import { hydrateScene } from '@/modules/scene'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared'
import { ArrowRight, Home } from '@/shared/lib/icons'
import { WindowControls } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { LivingRail } from './living-rail'
import { LivingStage } from './living-stage'
import { $livingView } from './living-store'
import styles from './living.module.css'
import { SceneBackdrop } from './scene-backdrop'

export function LivingRoot(): React.JSX.Element {
  useWindowMouseCapture(1, { setIgnoreMouseEvents: window.spiritagent?.surface?.setIgnoreMouseEvents })
  const shellRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('living-shell', shellRef, undefined, undefined, 1)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
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
