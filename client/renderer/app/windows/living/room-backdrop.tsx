// 房间背景：生活空间右栏的整窗底图（含角色），后端生成的房间图按 status 状态机切换。
//
// 状态机：none → pending → ready；中途换装 invalidated → pending → ready；
// 失败 failed → 玻璃底 + 角色 utterance，可可继续重试。
// 渲染策略：ready 优先用预模糊的烘焙位图（模糊一次成型，Ken Burns 只变换静态层，
// 避免合成器对被模糊层逐帧重采样）；烘焙未就绪或失败时回退 background-image + CSS filter。
// pending 在旧图上做亮度呼吸，failed 退回液态玻璃；溶解过渡 800ms。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $activeBackdrop, $backdropStatus } from '@/modules/room'
import { $noBlur } from '@/shared/lib/apply-no-blur'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'

import { useBakedBackdrop } from './baked-backdrop'
import styles from './room-backdrop.module.css'

export function RoomBackdrop(): React.JSX.Element {
  const status = useStore($backdropStatus)
  const backdrop = useStore($activeBackdrop)
  const theme = useStore($theme)
  const noBlur = useStore($noBlur)
  const t = useStrings().living.roomBackdrop
  const [prevUrl, setPrevUrl] = useState<null | string>(null)
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

  // ready 切换时记录上一张 URL，供 pending 期间继续展示。
  useEffect(() => {
    if (status === 'ready' && backdrop?.url) {
      setPrevUrl(backdrop.url)
    }
  }, [status, backdrop?.url])

  const reducedMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const bgUrl = status === 'pending' && prevUrl ? prevUrl : (backdrop?.url ?? null)
  const showKenBurns = !reducedMotion && status === 'ready' && bgUrl !== null

  const baked = useBakedBackdrop(noBlur ? null : bgUrl, viewport.width, viewport.height, theme)

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
      {status === 'failed' && (
        <div className={styles.failedGlass}>
          <p className={styles.failedText}>{t.failedText}</p>
        </div>
      )}
      {status === 'pending' && !prevUrl && (
        <div className={styles.pendingGlass}>
          <p className={styles.pendingText}>{t.pendingText}</p>
        </div>
      )}
    </div>
  )
}
