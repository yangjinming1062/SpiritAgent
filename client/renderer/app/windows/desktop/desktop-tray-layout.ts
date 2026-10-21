import { type RefObject, useLayoutEffect } from 'react'

import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import type { DesktopTrayLayout } from '@ipc/contracts'

const layoutOwners = new Map<number, symbol>()

/** 原生窗口盖在共同玻璃底板上；只上报尺寸和裁剪，不绘制或代理原生图标。 */
export function useDesktopTrayLayout({
  available,
  barRef,
  enabled,
  epoch,
  trayRef,
  viewportRef
}: {
  available: boolean
  barRef: RefObject<HTMLElement | null>
  enabled: boolean
  epoch: number
  trayRef: RefObject<HTMLDivElement | null>
  viewportRef: RefObject<HTMLDivElement | null>
}): void {
  useLayoutEffect(() => {
    const bar = barRef.current
    const tray = trayRef.current
    const viewport = viewportRef.current

    if (desktopPlatform() !== 'win32' || !enabled || !available || !bar || !tray || !viewport) {
      return
    }

    const owner = Symbol()
    layoutOwners.set(epoch, owner)
    let disposed = false
    let frame = 0
    let lastSignature: string | undefined
    let queued: DesktopTrayLayout['layout'] | undefined
    let measuredViewport: DesktopTrayLayout['viewport'] = null
    let inFlight: Promise<void> | undefined

    const flush = (): void => {
      if (inFlight || disposed || queued === undefined) {
        return
      }

      const layout = queued
      const measured = measuredViewport
      queued = undefined
      inFlight = shellApi()
        .presentation.setTrayLayout({ epoch, layout, viewport: measured })
        .catch(error => {
          lastSignature = undefined
          console.warn('[desktop] native tray layout failed', error)
        })
        .finally(() => {
          inFlight = undefined
          flush()
        })
    }

    const update = (): void => {
      frame = 0

      if (disposed || $presentation.get().stageEpoch !== epoch) {
        return
      }

      const rect = tray.getBoundingClientRect()
      const visible = viewport.getBoundingClientRect()
      const left = Math.max(0, visible.left)
      const top = Math.max(0, visible.top)
      const right = Math.min(window.innerWidth, visible.right)
      const bottom = Math.min(window.innerHeight, visible.bottom)

      const layout =
        rect.width > 0 && rect.height > 0 && right > left && bottom > top
          ? {
              bounds: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
              clip: { x: left, y: top, width: right - left, height: bottom - top }
            }
          : null

      const signature = JSON.stringify(layout)

      if (signature !== lastSignature) {
        lastSignature = signature
        // 视口尺寸随窗口变化上报，主进程据此丢弃压缩期间的旧测量。
        queued = layout
        measuredViewport = { width: window.innerWidth, height: window.innerHeight }
        flush()
      }
    }

    const schedule = (): void => {
      if (!frame) {
        frame = requestAnimationFrame(update)
      }
    }

    const observer = new ResizeObserver(schedule)
    observer.observe(bar)
    observer.observe(tray)
    observer.observe(viewport)
    viewport.addEventListener('scroll', schedule)
    window.addEventListener('resize', schedule)
    schedule()

    return () => {
      disposed = true
      queued = undefined
      cancelAnimationFrame(frame)
      observer.disconnect()
      viewport.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      void (inFlight ?? Promise.resolve()).then(async () => {
        // 同代次的新挂载会接管；旧表面卸载不能撤销新表面的托盘。
        if (layoutOwners.get(epoch) !== owner) {
          return
        }

        layoutOwners.delete(epoch)

        if ($presentation.get().stageEpoch === epoch) {
          await shellApi()
            .presentation.setTrayLayout({ epoch, layout: null, viewport: null })
            .catch(error => console.warn('[desktop] native tray layout release failed', error))
        }
      })
    }
  }, [available, barRef, enabled, epoch, trayRef, viewportRef])
}
