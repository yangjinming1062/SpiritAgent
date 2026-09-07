// 生活空间根组件：房间背景 + 顶栏 + 左栏 + 右栏（视图路由）。
//
// 不在右栏挂 PuppetStage / Companion3D；立绘由房间背景图承担。
// 关掉时主进程互斥会把焦点还给工作台或精灵。

import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { $spriteState } from '@/companion'
import { hydrateRoomBackdrop } from '@/living/room-backdrop-store'
import { ArrowRight, Home } from '@/shared/lib/icons'
import { WindowControls } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { requestOpenSurface } from '@/shared/store/surfaces'

import { LivingRail } from './living-rail'
import { LivingStage } from './living-stage'
import styles from './living.module.css'
import { RoomBackdrop } from './room-backdrop'

export function LivingRoot(): React.JSX.Element {
  const auth = useStore($auth)
  const spriteState = useStore($spriteState)

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void hydrateRoomBackdrop()
    }
  }, [auth.kind])

  const statusLabel = spriteState === 'thinking' || spriteState === 'working' ? '忙碌中' : '陪伴中'

  return (
    <div className={styles.windowFrame}>
      <div className={styles.shell} data-surface="living">
        <RoomBackdrop />

        <header
          className={styles.titlebar}
          onDoubleClick={() => {
            void window.spiritagent?.surface?.maximize?.()
          }}
        >
          <div className={styles.titleArea}>
            <Home className={styles.titleIcon} size={18} />
            <h1 className={styles.title}>生活空间</h1>
            <div className={styles.statusBadge}>
              <span className={styles.statusDot} />
              <span>{statusLabel}</span>
            </div>
          </div>
          <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
            <button
              className={styles.workbenchButton}
              onClick={() => {
                void requestOpenSurface('workbench')
              }}
              type="button"
            >
              <span>前往工作台</span>
              <ArrowRight size={13} />
            </button>
            <WindowControls />
          </div>
        </header>

        <div className={styles.body}>
          <LivingRail />
          <main className={styles.stage}>
            <LivingStage />
          </main>
        </div>
      </div>
    </div>
  )
}
