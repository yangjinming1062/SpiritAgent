import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useCallback, useEffect, useRef } from 'react'

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
import { useVideoPixelHitTest } from '@/modules/character/rendering/video'
import { useInteractiveRegion } from '@/shared'
import { $auth } from '@/shared/store/auth'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import styles from './workbench.module.css'

const VideoStage = lazy(() => import('@/modules/character/rendering/video').then(m => ({ default: m.VideoStage })))

export function WorkbenchCompanion(): React.JSX.Element {
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const videoStatus = useStore($actionCatalogStatus)
  const videoGenState = useStore($videoGenState)
  const videoGenStage = useStore($videoGenStage)
  const dict = useStrings()
  const t = dict.workbench
  const brandName = dict.brand.name
  const hasHydratedRef = useRef(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const videoHitTest = useVideoPixelHitTest(1)

  // 视频就绪挂视频层，否则落蛋形并给出真实状态（DESIGN「呈现与降级」）。
  const presentation = React.useMemo(
    () =>
      resolveCompanionPresentation({
        catalogStatus: videoStatus,
        generationStage: videoGenStage,
        generationState: videoGenState
      }),
    [videoStatus, videoGenState, videoGenStage]
  )

  const stageHitTest = useCallback(
    (x: number, y: number): boolean => {
      if (presentation.renderer === 'fallback') {
        return true
      }

      return videoHitTest(x, y)
    },
    [presentation.renderer, videoHitTest]
  )

  useInteractiveRegion('workbench-companion', wrapperRef, undefined, stageHitTest, 1)

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      hasHydratedRef.current = false

      return
    }

    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true

      void hydrateActionCatalog()
      void hydrateVideoPack()
      void ensureCompanionHydrated({
        hydratePersona,
        hydratePortrait
      })
    }

    return () => {
      hasHydratedRef.current = false
    }
  }, [auth.kind, lifecycle])

  return (
    <div
      className={styles.companionWrapper}
      onContextMenu={e => {
        e.preventDefault()
      }}
      ref={wrapperRef}
      title={t.companionTitle(brandName)}
    >
      <div className={styles.companionInner}>
        <Suspense fallback={null}>
          {presentation.renderer === 'fallback' ? (
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
              status={presentation.fallbackStatus}
              windowId={1}
            />
          ) : (
            <VideoStage />
          )}
        </Suspense>
        <SpriteVfxOverlay />
      </div>
    </div>
  )
}
