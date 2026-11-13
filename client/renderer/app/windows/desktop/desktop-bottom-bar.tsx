import type React from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import { ArrowLeft, ArrowRight } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { useStrings } from '@/shared/strings'
import type { PresentationState } from '@ipc/contracts'

import { DesktopDock } from './desktop-dock'
import { useDesktopTrayLayout } from './desktop-tray-layout'
import styles from './desktop.module.css'

export function DesktopBottomBar({
  avatar,
  navigation,
  menuEnabled,
  dockOverlayActive,
  onDockOpen,
  onDockOpenChange,
  stageEpoch,
  systemTray
}: {
  avatar: React.ReactNode
  navigation: React.ReactNode
  menuEnabled: boolean
  dockOverlayActive: boolean
  onDockOpen: () => void
  onDockOpenChange: (open: boolean) => void
  stageEpoch: number
  systemTray: PresentationState['systemTray']
}): React.JSX.Element {
  const t = useStrings().desktopShell
  const [dockWidth, setDockWidth] = useState(0)
  const [navigationWidth, setNavigationWidth] = useState(0)
  const [scroll, setScroll] = useState({ left: false, right: false })
  const trayAvailable = systemTray !== null
  const barRef = useRef<HTMLElement>(null)
  const itemsRef = useRef<HTMLDivElement>(null)
  const navigationRef = useRef<HTMLDivElement>(null)
  const wingRef = useRef<HTMLDivElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const trayRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-bottom-bar', barRef)
  useDesktopTrayLayout({
    available: trayAvailable,
    barRef,
    enabled: menuEnabled,
    epoch: stageEpoch,
    trayRef,
    viewportRef
  })

  useLayoutEffect(() => {
    const nav = navigationRef.current
    const wing = wingRef.current
    const viewport = viewportRef.current
    const items = itemsRef.current

    if (!nav || !wing || !viewport || !items) {
      return
    }

    const update = (): void => {
      // 托盘贴住翼的右端，行会被拉伸成整翼；翼宽只能按导航与托盘的固有宽度累加。
      const tray = trayRef.current
      const gap = tray ? Number.parseFloat(getComputedStyle(items).getPropertyValue('--desktop-item-gap')) || 0 : 0
      const contentWidth = nav.getBoundingClientRect().width + (tray ? tray.getBoundingClientRect().width + gap : 0)
      setNavigationWidth(contentWidth)

      const overflow = contentWidth > wing.clientWidth + 1

      const next = {
        left: overflow && viewport.scrollLeft > 1,
        right: overflow && viewport.scrollWidth - viewport.clientWidth - viewport.scrollLeft > 1
      }

      setScroll(previous => (previous.left === next.left && previous.right === next.right ? previous : next))
    }

    const observer = new ResizeObserver(update)
    observer.observe(nav)
    observer.observe(wing)
    observer.observe(viewport)

    if (trayAvailable && trayRef.current) {
      observer.observe(trayRef.current)
    }

    viewport.addEventListener('scroll', update)
    update()

    return () => {
      observer.disconnect()
      viewport.removeEventListener('scroll', update)
    }
  }, [trayAvailable])

  const overflow = scroll.left || scroll.right

  const scrollWing = (direction: -1 | 1): void => {
    const viewport = viewportRef.current

    if (viewport) {
      viewport.scrollBy({ left: direction * Math.max(48, viewport.clientWidth * 0.8), behavior: 'instant' })
    }
  }

  return (
    <footer
      className={styles.bottomBar}
      ref={barRef}
      style={{ '--desktop-wing-width': `${Math.ceil(Math.max(dockWidth, navigationWidth))}px` } as React.CSSProperties}
    >
      <div aria-hidden="true" className={styles.bottomBarBackdrop} />
      <DesktopDock
        menuEnabled={menuEnabled}
        onBeforeOpen={onDockOpen}
        onContentWidthChange={setDockWidth}
        onMenuOpenChange={onDockOpenChange}
        overlayActive={dockOverlayActive}
      />
      <div className={styles.avatarSlot}>{avatar}</div>
      <div className={styles.productWing} ref={wingRef}>
        <button
          aria-label={t.moveLeft}
          className={styles.wingScroll}
          disabled={!scroll.left}
          hidden={!overflow}
          onClick={() => scrollWing(-1)}
          title={t.moveLeft}
          type="button"
        >
          <ArrowLeft size={14} />
        </button>
        <div
          className={styles.productViewport}
          onWheel={event => {
            const viewport = viewportRef.current

            if (
              viewport &&
              viewport.scrollWidth > viewport.clientWidth &&
              Math.abs(event.deltaY) > Math.abs(event.deltaX)
            ) {
              viewport.scrollLeft += event.deltaY
            }
          }}
          ref={viewportRef}
        >
          <div className={styles.productItems} ref={itemsRef}>
            <div className={styles.productGroup} ref={navigationRef}>
              {navigation}
            </div>
            {systemTray && (
              <div
                aria-hidden="true"
                className={styles.systemTraySlot}
                ref={trayRef}
                style={{ width: systemTray.width, height: systemTray.height }}
              />
            )}
          </div>
        </div>
        <button
          aria-label={t.moveRight}
          className={styles.wingScroll}
          disabled={!scroll.right}
          hidden={!overflow}
          onClick={() => scrollWing(1)}
          title={t.moveRight}
          type="button"
        >
          <ArrowRight size={14} />
        </button>
      </div>
    </footer>
  )
}
