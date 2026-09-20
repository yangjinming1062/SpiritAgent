import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useCallback, useEffect, useRef } from 'react'

import {
  $companionLifecycle,
  $videoPackStatus,
  EggStage,
  emitVfx,
  ensureCompanionHydrated,
  handlePetInteraction,
  hydratePersona,
  hydratePortrait,
  hydrateVideoPack,
  reportUserActivity,
  resolveCompanionPresentation,
  SpriteVfxOverlay
} from '@/modules/character'
import { $videoHitTest } from '@/modules/character/rendering/video'
import { useInteractiveRegion } from '@/shared'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import styles from './workbench.module.css'

const VideoStage = lazy(() => import('@/modules/character/rendering/video').then(m => ({ default: m.VideoStage })))

export function WorkbenchCompanion(): React.JSX.Element {
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const videoStatus = useStore($videoPackStatus)
  const dict = useStrings()
  const t = dict.workbench
  const brandName = dict.brand.name
  const pointerStartRef = useRef<{ time: number; x: number; y: number } | null>(null)
  const hasHydratedRef = useRef(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const hitVideoRef = useRef<((x: number, y: number) => boolean | null) | null>(null)

  useEffect(
    () =>
      $videoHitTest.subscribe(fn => {
        hitVideoRef.current = fn
      }),
    []
  )

  // 视频就绪挂视频层，否则落程序化蛋兜底（DESIGN §1.2「永不空白」）。
  const presentation = React.useMemo(
    () => resolveCompanionPresentation({ videoReady: videoStatus === 'ready' }),
    [videoStatus]
  )

  const stageHitTest = useCallback(
    (x: number, y: number): boolean => {
      if (auth.kind !== 'authenticated') {
        return true
      }

      const probeVideo = hitVideoRef.current

      if (probeVideo) {
        const result = probeVideo(x, y)

        if (result !== null) {
          return result
        }
      }

      return true
    },
    [auth.kind]
  )

  useInteractiveRegion('workbench-companion', wrapperRef, undefined, stageHitTest, 1)

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      hasHydratedRef.current = false

      return
    }

    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true

      void ensureCompanionHydrated({
        hydratePersona,
        hydratePortrait
      })
      void hydrateVideoPack()
    }

    return () => {
      hasHydratedRef.current = false
    }
  }, [auth.kind, lifecycle])

  const handleTap = (): void => {
    reportUserActivity()

    if (auth.kind === 'authenticated') {
      handlePetInteraction()
    } else {
      emitVfx('heart', { count: 3, nx: 0.5, ny: 0.25 })
    }
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) {
      return
    }

    pointerStartRef.current = { time: performance.now(), x: e.clientX, y: e.clientY }
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>): void => {
    const start = pointerStartRef.current
    pointerStartRef.current = null

    if (!start) {
      return
    }

    const dist = Math.hypot(e.clientX - start.x, e.clientY - start.y)
    const elapsed = performance.now() - start.time

    // 微小位移且短按视为点击戳击/摸头反馈；长按或位移则由系统原生拖拽接管
    if (dist <= 6 && elapsed < 400) {
      handleTap()
    }
  }

  return (
    <div
      className={styles.companionWrapper}
      onContextMenu={e => {
        e.preventDefault()
      }}
      onPointerCancel={() => {
        pointerStartRef.current = null
      }}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      ref={wrapperRef}
      title={t.companionTitle(brandName)}
    >
      <div className={styles.companionInner}>
        <Suspense fallback={null}>
          {auth.kind !== 'authenticated' || presentation.renderer === 'fallback' ? (
            <EggStage onTap={handleTap} />
          ) : (
            <VideoStage />
          )}
        </Suspense>
        <SpriteVfxOverlay />
      </div>
    </div>
  )
}
