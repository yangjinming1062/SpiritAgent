// 当前场景与创建任务独立；生成和分析失败保留当前背景。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $activeScene, $sceneTaskStatus } from '@/modules/scene'
import { $noBlur } from '@/shared/lib/apply-no-blur'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'

import { useBakedScene } from './baked-backdrop'
import styles from './scene-backdrop.module.css'

export function SceneBackdrop(): React.JSX.Element {
  const taskStatus = useStore($sceneTaskStatus)
  const backdrop = useStore($activeScene)
  const theme = useStore($theme)
  const noBlur = useStore($noBlur)
  const t = useStrings().living.sceneBackdrop
  const [viewport, setViewport] = useState({ height: 0, width: 0 })
  const rootRef = useRef<HTMLDivElement>(null)

  // 烘焙尺寸按容器：root 撑满窗口，用 ResizeObserver 跟踪（最大化切换也覆盖）。
  useEffect(() => {
    const el = rootRef.current

    if (!el) {
      return
    }

    const ro = new ResizeObserver(() => {
      setViewport({ height: el.clientHeight, width: el.clientWidth })
    })

    ro.observe(el)

    return () => ro.disconnect()
  }, [])

  const reducedMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const bgUrl = backdrop?.url ?? null
  const status = bgUrl ? 'ready' : taskStatus === 'pending' ? 'pending' : 'none'
  const showKenBurns = !reducedMotion && status === 'ready' && bgUrl !== null

  const baked = useBakedScene(noBlur ? null : bgUrl, viewport.width, viewport.height, theme)

  return (
    <div aria-hidden="true" className={styles.root} ref={rootRef}>
      <div
        className={`${styles.backdropImage} ${showKenBurns ? styles.kenBurns : ''} ${styles[`status_${status}`] ?? ''}`}
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
