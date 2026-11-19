import { type RefObject, useLayoutEffect } from 'react'

import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import type { DesktopTrayLayout } from '@ipc/contracts'

const layoutOwners = new Map<number, symbol>()

/** 原生托盘底边对齐 Dock 托板，滚动视口决定可见区域。 */
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
    let queued: DesktopTrayLayout | undefined
    let inFlight: Promise<void> | undefined

    const flush = (): void => {
      if (inFlight || disposed || queued === undefined) {
        return
      }

      const next = queued
      queued = undefined
      inFlight = shellApi()
        .presentation.setTrayLayout(next)
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
      const viewportLeft = visible.left + viewport.clientLeft
      const viewportTop = visible.top + viewport.clientTop
      const left = Math.max(0, viewportLeft, rect.left)
      const top = Math.max(0, viewportTop, rect.top)
      const right = Math.min(window.innerWidth, viewportLeft + viewport.clientWidth, rect.right)
      const bottom = Math.min(window.innerHeight, viewportTop + viewport.clientHeight, rect.bottom)

      const layout =
        right > left && bottom > top
          ? {
              bounds: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
              clip: { x: left, y: top, width: right - left, height: bottom - top }
            }
          : null

      const next = { epoch, layout, viewport: { width: window.innerWidth, height: window.innerHeight } }
      const signature = JSON.stringify(next)

      if (signature !== lastSignature) {
        lastSignature = signature
        // 视口尺寸随窗口变化上报，主进程据此丢弃压缩期间的旧测量。
        queued = next
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
