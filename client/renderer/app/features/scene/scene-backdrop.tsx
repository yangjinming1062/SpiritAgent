// 窗口模式的环境来源是当前影像冻结的环境素材；素材缺失时保持透明，不回退到环境库图片。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $windowVisualProjection, useVisualProjection } from '@/app/workflows/visual-presentation'
import { $noBlur } from '@/shared/lib/apply-no-blur'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'

import { useBakedScene } from './baked-backdrop'
import styles from './scene-backdrop.module.css'

// 拖拽改尺寸按 64px 网格重烘焙，停稳后吸附回精确尺寸，终态烘焙无偏差。
const BAKE_SIZE_GRID = 64
const RESIZE_SETTLE_MS = 200

function useFrozenScene(): { ready: boolean; url: string | null } {
  const projection = useStore($windowVisualProjection)
  const auth = useStore($auth)
  const path = projection?.scene_path ?? null
  const ready = projection?.ready ?? false
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let current = true

    if (!path || auth.kind !== 'authenticated') {
      setUrl(null)

      return () => {
        current = false
      }
    }

    void backendApi()
      .apiAsset({ url: path, preferCache: true })
      .then(next => {
        if (current) {
          setUrl(next)
        }
      })
      .catch(error => log.warn('scene-backdrop', 'Frozen scene asset unavailable', error))

    return () => {
      current = false
    }
  }, [path, auth.kind])

  return { ready, url }
}

export function SceneBackdrop(): React.JSX.Element {
  useVisualProjection()
  const { ready, url: bgUrl } = useFrozenScene()
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

  const status = bgUrl ? 'ready' : ready ? 'none' : 'pending'

  const baked = useBakedScene(noBlur ? null : bgUrl, viewport.width, viewport.height, theme)

  return (
    <div aria-hidden="true" className={styles.root} ref={rootRef}>
      <div
        className={cn(styles.backdropImage, styles[`status_${status}`])}
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
