// 生活空间根组件：房间背景 + 顶栏 + 左栏 + 右栏（视图路由）。
//
// 不在右栏挂 PuppetStage / Companion3D；立绘由房间背景图承担。
// 关掉时主进程互斥会把焦点还给工作台或精灵。

import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { SpriteStatusBadge } from '@/modules/character'
import { MediaViewerOverlay } from '@/modules/media'
import { hydrateRoomBackdrop } from '@/modules/room'
import { ArrowRight, Home } from '@/shared/lib/icons'
import { WindowControls } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { LivingRail } from './living-rail'
import { LivingStage } from './living-stage'
import { $livingView } from './living-store'
import styles from './living.module.css'
import { RoomBackdrop } from './room-backdrop'

export function LivingRoot(): React.JSX.Element {
  const auth = useStore($auth)
  const livingView = useStore($livingView)
  const dict = useStrings()
  const t = dict.living

  useEffect(() => {
    document.title = `${dict.brand.name} · ${t.title}`
  }, [dict.brand.name, t.title])

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void hydrateRoomBackdrop()
    }
  }, [auth.kind])

  useEffect(() => {
    const root = document.documentElement
    root.dataset.livingView = livingView

    return () => {
      delete root.dataset.livingView
    }
  }, [livingView])

  return (
    <div className={styles.windowFrame}>
      <MediaViewerOverlay />
      <RoomBackdrop />
      <div aria-hidden="true" className={styles.glassPlate} />
      <div className={styles.shell} data-living-view={livingView} data-surface="living">
        <header
          className={styles.titlebar}
          onDoubleClick={() => {
            void window.spiritagent?.surface?.maximize?.()
          }}
        >
          <div className={styles.titleArea}>
            <Home className={styles.titleIcon} size={18} />
            <h1 className={styles.title}>{t.title}</h1>
            <SpriteStatusBadge />
          </div>
          <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
            <button
              className={styles.workbenchButton}
              onClick={() => {
                void requestOpenSurface('workbench')
              }}
              type="button"
            >
              <span>{t.goToWorkbench}</span>
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
