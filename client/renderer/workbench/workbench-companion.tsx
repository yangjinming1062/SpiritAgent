import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef } from 'react'

import { $mesh2dHitmap, $puppetReady, $renderMode, hydrateMesh2D, hydratePuppet } from '@/2d'
import { $glbLoadFailed, $modelInfo, $sprite3DHitTest, hydrateModel } from '@/3d'
import {
  $companionLifecycle,
  emitVfx,
  ensureCompanionHydrated,
  handlePetInteraction,
  hydratePersona,
  hydratePortrait,
  Mesh2DVfxOverlay,
  reportUserActivity,
  resolveCompanionRenderLayer
} from '@/companion'
import { EggStage } from '@/onboarding'
import { useInteractiveRegion } from '@/shared'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import styles from './workbench.module.css'

const Companion3D = lazy(() => import('@/3d').then(m => ({ default: m.Companion3D })))
const PuppetStage = lazy(() => import('@/2d').then(m => ({ default: m.PuppetStage })))

export function WorkbenchCompanion(): React.JSX.Element {
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const renderMode = useStore($renderMode)
  const puppetReady = useStore($puppetReady)
  const modelInfo = useStore($modelInfo)
  const glbLoadFailed = useStore($glbLoadFailed)
  const dict = useStrings()
  const t = dict.workbench
  const brandName = dict.brand.name
  const pointerStartRef = useRef<{ time: number; x: number; y: number } | null>(null)
  const hasHydratedRef = useRef(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const hit3DRef = useRef<((x: number, y: number) => boolean | null) | null>(null)

  useEffect(
    () =>
      $sprite3DHitTest.subscribe(fn => {
        hit3DRef.current = fn
      }),
    []
  )

  const renderLayer = useMemo<'companion3d' | 'puppet'>(() => {
    return resolveCompanionRenderLayer({
      glbLoadFailed,
      modelStatus: modelInfo.status,
      puppetReady,
      renderMode
    })
  }, [renderMode, puppetReady, glbLoadFailed, modelInfo.status])

  const stageHitTest = useCallback(
    (x: number, y: number): boolean => {
      if (auth.kind !== 'authenticated') {
        return true
      }

      if (renderLayer === 'puppet') {
        const hitmap = $mesh2dHitmap.get()

        if (!hitmap) {
          return true
        }

        const rect = wrapperRef.current?.getBoundingClientRect()

        if (!rect || rect.width <= 0 || rect.height <= 0) {
          return false
        }

        return hitmap.hit((x - rect.left) / rect.width, (y - rect.top) / rect.height) !== null
      }

      const probe3d = hit3DRef.current

      if (probe3d) {
        return probe3d(x, y) ?? true
      }

      return true
    },
    [auth.kind, renderLayer]
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
        hydrateMesh2D,
        hydrateModel,
        hydratePersona,
        hydratePortrait,
        hydratePuppet
      })
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
          {auth.kind !== 'authenticated' ? (
            <EggStage onTap={handleTap} />
          ) : renderLayer === 'puppet' ? (
            <PuppetStage />
          ) : (
            <Companion3D />
          )}
        </Suspense>
        <Mesh2DVfxOverlay />
      </div>
    </div>
  )
}
