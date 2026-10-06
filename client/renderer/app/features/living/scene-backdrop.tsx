// 当前场景与创建任务独立；生成和分析失败保留当前背景。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $activeScene, $sceneTaskStatus } from '@/modules/scene'
import { $noBlur } from '@/shared/lib/apply-no-blur'
import { cn } from '@/shared/lib/utils'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'

import { useBakedScene } from './baked-backdrop'
import styles from './scene-backdrop.module.css'

// 拖拽改尺寸按 64px 网格重烘焙，停稳后吸附回精确尺寸，终态烘焙无偏差。
const BAKE_SIZE_GRID = 64
const RESIZE_SETTLE_MS = 200
const reducedMotionMql = typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null

export function SceneBackdrop(): React.JSX.Element {
  const taskStatus = useStore($sceneTaskStatus)
  const backdrop = useStore($activeScene)
  const theme = useStore($theme)
  const noBlur = useStore($noBlur)
  const t = useStrings().living.sceneBackdrop
  const [viewport, setViewport] = useState({ height: 0, width: 0 })
  const rootRef = useRef<HTMLDivElement>(null)

  // 烘焙尺寸按容器 ResizeObserver 跟踪（最大化切换也覆盖），量化与吸附见 BAKE_SIZE_GRID。
  useEffect(() => {
    const el = rootRef.current

    if (!el) {
      return
    }

    let settleTimer: number | undefined

    const ro = new ResizeObserver(() => {
      const height = el.clientHeight
      const width = el.clientWidth

      const quantized = {
        height: height - (height % BAKE_SIZE_GRID),
        width: width - (width % BAKE_SIZE_GRID)
      }

      setViewport(previous =>
        previous.height === quantized.height && previous.width === quantized.width ? previous : quantized
      )

      window.clearTimeout(settleTimer)
      settleTimer = window.setTimeout(() => {
        setViewport({ height, width })
      }, RESIZE_SETTLE_MS)
    })

    ro.observe(el)

    return () => {
      window.clearTimeout(settleTimer)
      ro.disconnect()
    }
  }, [])

  const reducedMotion = reducedMotionMql?.matches ?? false

  const bgUrl = backdrop?.url ?? null
  const status = bgUrl ? 'ready' : taskStatus === 'pending' ? 'pending' : 'none'
  const showKenBurns = !reducedMotion && status === 'ready'

  const baked = useBakedScene(noBlur ? null : bgUrl, viewport.width, viewport.height, theme)

  return (
    <div aria-hidden="true" className={styles.root} ref={rootRef}>
      <div
        className={cn(styles.backdropImage, showKenBurns && styles.kenBurns, styles[`status_${status}`])}
        data-baked={baked ? 'true' : undefined}
        style={
          baked
            ? { backgroundImage: `url(${baked.dataUrl})` }
            : bgUrl
              ? { backgroundImage: `url(${bgUrl})` }
              : undefined
        }
      />
      {status === 'pending' && !bgUrl && (
        <div className={styles.pendingGlass}>
          <p className={styles.pendingText}>{t.pendingText}</p>
        </div>
      )}
    </div>
  )
}
