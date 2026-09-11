// 房间背景：生活空间右栏的整窗底图（含角色），后端生成的房间图按 status 状态机切换。
//
// 状态机：none → pending → ready；中途换装 invalidated → pending → ready；
// 失败 failed → 玻璃底 + 角色 utterance，可可继续重试。
// 渲染策略：ready 用 background-image cover，pending 在旧图上做亮度呼吸，
// failed 退回液态玻璃；溶解过渡 800ms。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { $activeBackdrop, $backdropStatus } from '@/living/room-backdrop-store'
import { useStrings } from '@/shared/strings'

import styles from './room-backdrop.module.css'

export function RoomBackdrop(): React.JSX.Element {
  const status = useStore($backdropStatus)
  const backdrop = useStore($activeBackdrop)
  const t = useStrings().living.roomBackdrop
  const [prevUrl, setPrevUrl] = useState<null | string>(null)

  // ready 切换时记录上一张 URL，供 pending 期间继续展示。
  useEffect(() => {
    if (status === 'ready' && backdrop?.url) {
      setPrevUrl(backdrop.url)
    }
  }, [status, backdrop?.url])

  const reducedMotion = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const bgUrl = status === 'pending' && prevUrl ? prevUrl : (backdrop?.url ?? null)
  const showKenBurns = !reducedMotion && status === 'ready' && bgUrl !== null

  return (
    <div aria-hidden="true" className={styles.root}>
      <div
        className={`${styles.backdropImage} ${showKenBurns ? styles.kenBurns : ''} ${styles[`status_${status}`] ?? ''}`}
        style={bgUrl ? { backgroundImage: `url(${bgUrl})` } : undefined}
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
