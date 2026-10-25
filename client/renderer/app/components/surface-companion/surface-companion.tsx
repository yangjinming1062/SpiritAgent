import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { SceneBackdrop } from '@/app/features/scene/scene-backdrop'
import { $companionLifecycle, ensureCompanionHydrated } from '@/modules/character'
import { OutfitStage } from '@/modules/character/rendering'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $mainStageVisible, reportCompanionVisibility } from '@/shared/store/surfaces'

import styles from './surface-companion.module.css'

export function SurfaceCompanion({ visible }: { visible: boolean }): React.JSX.Element | null {
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const ready = sessionId !== null && lifecycle === 'ready'
  const root = useRef<HTMLDivElement>(null)
  const [intersecting, setIntersecting] = useState(false)
  const stageVisible = ready && visible && intersecting

  useEffect(() => {
    const element = root.current

    if (!element) {
      return
    }

    const observer = new IntersectionObserver(([entry]) =>
      setIntersecting(entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0)
    )

    observer.observe(element)

    return () => observer.disconnect()
  }, [ready])

  useLayoutEffect(() => {
    $mainStageVisible.set(stageVisible)

    return () => $mainStageVisible.set(false)
  }, [stageVisible])

  useEffect(() => {
    if (!sessionId) {
      return
    }

    void reportCompanionVisibility(sessionId, stageVisible).catch(error =>
      log.warn('life-area', 'Visibility report failed', error)
    )
  }, [sessionId, stageVisible])

  useEffect(
    () => () => {
      if (sessionId) {
        void reportCompanionVisibility(sessionId, false).catch(error =>
          log.warn('life-area', 'Visibility release failed', error)
        )
      }
    },
    [sessionId]
  )

  useEffect(() => {
    if (auth.kind === 'authenticated' && lifecycle === 'ready') {
      void ensureCompanionHydrated()
    }
  }, [auth, lifecycle])

  return ready ? (
    <div className={styles.stage} ref={root}>
      <SceneBackdrop />
      <div className={styles.figure}>
        <OutfitStage contentAlign="left" eggSize="min(150px, 60%)" windowId={1} />
      </div>
    </div>
  ) : null
}
