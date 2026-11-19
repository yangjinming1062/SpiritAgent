import type React from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import { ArrowLeft, ArrowRight } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { useStrings } from '@/shared/strings'
import { DESKTOP_LAYOUT } from '@client-shared/desktop-layout'
import type { PresentationState } from '@ipc/contracts'

import { DesktopDock } from './desktop-dock'
import { useDockMagnification } from './desktop-dock-magnification'
import { useDesktopTrayLayout } from './desktop-tray-layout'
import styles from './desktop.module.css'

export function DesktopBottomBar({
  avatar,
  chat,
  navigation,
  menuEnabled,
  dockOverlayActive,
  onDockOpen,
  onDockOpenChange,
  overlayActive,
  railIconSize,
  stageEpoch,
  systemTray
}: {
  avatar: React.ReactNode
  chat: React.ReactNode
  navigation: React.ReactNode
  menuEnabled: boolean
  dockOverlayActive: boolean
  onDockOpen: () => void
  onDockOpenChange: (open: boolean) => void
  overlayActive: boolean
  railIconSize: number
  stageEpoch: number
  systemTray: PresentationState['systemTray']
}): React.JSX.Element {
  const t = useStrings().desktopShell
  const [dockWidth, setDockWidth] = useState(0)
  const [navigationWidth, setNavigationWidth] = useState(0)
  const [scroll, setScroll] = useState({ left: false, right: false, arrows: false })
  const trayAvailable = systemTray !== null
  const barRef = useRef<HTMLElement>(null)
  const itemsRef = useRef<HTMLDivElement>(null)
  const navigationRef = useRef<HTMLDivElement>(null)
  const wingRef = useRef<HTMLDivElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const trayRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion(
    'desktop-bottom-bar',
    barRef,
    element => {
      const { x, y, width, height } = element.getBoundingClientRect()

      return new DOMRect(x, y, width, height + DESKTOP_LAYOUT.wingScrollbarGutter)
    },
    (x, y) => {
      const target = document.elementFromPoint(x, y)
      const viewport = viewportRef.current

      if (viewport && target === viewport) {
        // 滚动条接收鼠标，滚动视口中的空白交给桌面。
        const rect = viewport.getBoundingClientRect()

        const scrollbarTop = Math.min(
          rect.top + viewport.clientHeight,
          rect.bottom - DESKTOP_LAYOUT.wingScrollbarGutter
        )

        return viewport.scrollWidth > viewport.clientWidth && y >= scrollbarTop
      }

      return Boolean(target && barRef.current?.contains(target))
    }
  )
  useDockMagnification(barRef, menuEnabled && !overlayActive)
  useDesktopTrayLayout({
    available: trayAvailable,
    barRef,
    enabled: menuEnabled,
    epoch: stageEpoch,
    trayRef,
    viewportRef
  })

  useLayoutEffect(() => {
    const wing = wingRef.current
    const viewport = viewportRef.current
    const items = itemsRef.current
    const nav = navigationRef.current
    const tray = trayRef.current

    if (!wing || !viewport || !items || !nav) {
      return
    }

    const update = (): void => {
      // 右翼展开后的弹性间距不计入固有宽度，避免托盘右对齐时推动整条底栏继续变宽。
      const style = getComputedStyle(items)
      const trayWidth = tray ? tray.getBoundingClientRect().width + Number.parseFloat(style.columnGap) : 0

      const contentWidth = Math.ceil(
        nav.getBoundingClientRect().width + trayWidth + Number.parseFloat(style.paddingInlineEnd)
      )

      const available = wing.clientWidth
      const left = viewport.scrollLeft > 1
      const right = viewport.scrollWidth - viewport.clientWidth - viewport.scrollLeft > 1

      const arrows =
        contentWidth > available + 1 &&
        available >= railIconSize + 2 * (DESKTOP_LAYOUT.wingScrollSize + DESKTOP_LAYOUT.wingScrollGap)

      setNavigationWidth(contentWidth)
      setScroll(previous =>
        previous.left === left && previous.right === right && previous.arrows === arrows
          ? previous
          : { left, right, arrows }
      )
    }

    const observer = new ResizeObserver(update)
    observer.observe(items)
    observer.observe(nav)
    observer.observe(wing)
    observer.observe(viewport)

    if (tray) {
      observer.observe(tray)
    }

    viewport.addEventListener('scroll', update)
    update()

    return () => {
      observer.disconnect()
      viewport.removeEventListener('scroll', update)
    }
  }, [railIconSize, trayAvailable])

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
      data-system-tray={trayAvailable}
      ref={barRef}
      style={
        {
          '--desktop-dock-width': `${Math.ceil(dockWidth)}px`,
          '--desktop-navigation-width': `${navigationWidth}px`
        } as React.CSSProperties
      }
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
      <div className={styles.rightRail} data-dock-motion-group>
        <div className={styles.chatSlot}>{chat}</div>
        <div className={styles.productWing} ref={wingRef}>
          <button
            aria-label={t.moveLeft}
            className={styles.wingScroll}
            disabled={!scroll.left}
            hidden={!scroll.arrows}
            onClick={() => scrollWing(-1)}
            title={t.moveLeft}
            type="button"
          >
            <ArrowLeft size={14} />
          </button>
          <div
            aria-label={t.title}
            className={styles.productViewport}
            data-dock-viewport
            onWheel={event => {
              const viewport = event.currentTarget

              if (viewport.scrollWidth > viewport.clientWidth && Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
                viewport.scrollLeft += event.deltaY
              }
            }}
            ref={viewportRef}
            role="group"
            tabIndex={overflow && !scroll.arrows ? 0 : undefined}
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
            hidden={!scroll.arrows}
            onClick={() => scrollWing(1)}
            title={t.moveRight}
            type="button"
          >
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </footer>
  )
}
