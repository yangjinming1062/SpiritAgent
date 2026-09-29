import type { SurfaceId } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import {
  $actionCatalogStatus,
  $companionLifecycle,
  $videoGenStage,
  $videoGenState,
  EggStage,
  ensureCompanionHydrated,
  hydrateActionCatalog,
  hydratePersona,
  hydratePortrait,
  hydrateVideoPack,
  resolveCompanionPresentation,
  SpriteVfxOverlay
} from '@/modules/character'
import { useVideoPixelHitTest, VideoStage } from '@/modules/character/rendering/video'
import { useInteractiveRegion } from '@/shared'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { $auth } from '@/shared/store/auth'
import { $surfaceCompanions, requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import styles from './surface-companion.module.css'

export function SurfaceCompanion({ surface }: { surface: SurfaceId }): React.JSX.Element | null {
  const state = useStore($surfaceCompanions)[surface]
  const hasSlot = state.slotWidth > 0
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const catalogStatus = useStore($actionCatalogStatus)
  const generationState = useStore($videoGenState)
  const generationStage = useStore($videoGenStage)
  const strings = useStrings()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const videoHitTest = useVideoPixelHitTest(1)

  const presentation = resolveCompanionPresentation({ catalogStatus, generationStage, generationState })

  const hitTest = useCallback(
    (x: number, y: number): boolean => state.visible && presentation.renderer === 'video' && videoHitTest(x, y),
    [presentation.renderer, state.visible, videoHitTest]
  )

  useInteractiveRegion('surface-companion', wrapperRef, undefined, hitTest, 1)

  useLayoutEffect(() => {
    probeInteractiveRegions(1)
  }, [state])

  useEffect(() => {
    const element = wrapperRef.current

    if (!element) {
      return
    }

    const observer = new ResizeObserver(() => probeInteractiveRegions(1))
    observer.observe(element)

    return () => observer.disconnect()
  }, [hasSlot])

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready' || !state.visible) {
      return
    }

    void hydrateActionCatalog()
    void hydrateVideoPack()
    void ensureCompanionHydrated({ hydratePersona, hydratePortrait })
  }, [auth.kind, lifecycle, state.visible])

  if (!hasSlot) {
    return null
  }

  const slotWidth = state.outerWidth ? `${(state.slotWidth / state.outerWidth) * 100}%` : state.slotWidth

  return (
    <aside className={styles.slot} style={{ order: state.preference.side === 'left' ? -1 : 1, width: slotWidth }}>
      <div
        className={styles.wrapper}
        onContextMenu={event => event.preventDefault()}
        ref={wrapperRef}
        title={strings.common.companionControl.dragTitle(strings.brand.name)}
      >
        <div className={styles.inner}>
          {state.visible &&
            (presentation.renderer === 'fallback' ? (
              <EggStage
                hasRecoveryAction={presentation.fallbackActionAvailable}
                message={presentation.fallbackMessage}
                onStatusAction={() => {
                  if (presentation.fallbackStatus === 'failed') {
                    void requestOpenSurface('living', { view: 'appearance' })

                    return
                  }

                  void hydrateActionCatalog(true)
                  void hydrateVideoPack(true)
                }}
                size="min(280px, 92%)"
                status={presentation.fallbackStatus}
                windowId={1}
              />
            ) : (
              <VideoStage />
            ))}
          {state.visible && <SpriteVfxOverlay />}
        </div>
      </div>
    </aside>
  )
}
