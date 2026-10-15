import type React from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import { useInteractiveRegion } from '@/shared/lib/interactive-regions'

import { DesktopDock } from './desktop-dock'
import styles from './desktop.module.css'

export function DesktopBottomBar({
  avatar,
  navigation,
  menuEnabled,
  dockOverlayActive,
  onDockOpen,
  onDockOpenChange
}: {
  avatar: React.ReactNode
  navigation: React.ReactNode
  menuEnabled: boolean
  dockOverlayActive: boolean
  onDockOpen: () => void
  onDockOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const [dockWidth, setDockWidth] = useState(0)
  const [navigationWidth, setNavigationWidth] = useState(0)
  const barRef = useRef<HTMLElement>(null)
  const navigationRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-bottom-bar', barRef)

  useLayoutEffect(() => {
    const element = navigationRef.current

    if (!element) {
      return
    }

    const update = (): void => setNavigationWidth(element.getBoundingClientRect().width)
    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [])

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
      <div className={styles.productViewport}>
        <div className={styles.productItems} ref={navigationRef}>
          {navigation}
        </div>
      </div>
    </footer>
  )
}
