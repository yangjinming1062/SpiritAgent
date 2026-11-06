import { screen } from 'electron'

import type { DesktopTrayLayout, PresentationState } from '@ipc/contracts'

import { DESKTOP_LAYOUT, getDesktopRailLayout } from '../../../shared/desktop-layout'
import type { DesktopHostSystemTray } from '../../shared/desktop-host'

export interface TrayBoundsInspection {
  outOfBand: boolean
  stale: boolean
  detail: string
}

export function validTrayRect(rect: unknown): rect is Electron.Rectangle {
  if (!rect || typeof rect !== 'object') {
    return false
  }

  const { x, y, width, height } = rect as Electron.Rectangle

  return (
    [x, y, width, height].every(item => typeof item === 'number' && Number.isFinite(item)) &&
    Math.abs(x) <= 1_000_000 &&
    Math.abs(y) <= 1_000_000 &&
    width > 0 &&
    width <= 65_536 &&
    height > 0 &&
    height <= DESKTOP_LAYOUT.bottomInset
  )
}

/** 底栏预留生效时窗口可能被系统压缩，旧测量要丢弃而不是判为越界。 */
export function inspectTrayBounds(
  layout: NonNullable<DesktopTrayLayout['layout']>,
  win: Electron.BrowserWindow,
  display: Electron.Display,
  viewport: DesktopTrayLayout['viewport'] | undefined
): TrayBoundsInspection {
  const { bounds, clip } = layout
  const minimumY = display.bounds.height - DESKTOP_LAYOUT.bottomInset - 1
  const content = win.getContentBounds()

  const outOfBand =
    clip.x < -1 ||
    clip.y < minimumY ||
    clip.x + clip.width > display.bounds.width + 1 ||
    clip.y + clip.height > display.bounds.height + 1 ||
    bounds.y < minimumY ||
    bounds.y + bounds.height > display.bounds.height + 1

  if (!outOfBand) {
    return { outOfBand: false, stale: false, detail: '' }
  }

  const compressed = content.y + content.height < display.bounds.y + display.bounds.height - 2

  const stale =
    compressed || (viewport !== undefined && viewport !== null && Math.abs(viewport.height - content.height) > 2)

  return {
    outOfBand: true,
    stale: Boolean(stale),
    detail:
      `display=${JSON.stringify(display.bounds)} window=${JSON.stringify(content)} ` +
      `bounds=${JSON.stringify(bounds)} clip=${JSON.stringify(clip)}`
  }
}

export function trayLayoutToPhysical(
  layout: DesktopTrayLayout['layout'],
  win: Electron.BrowserWindow,
  display: Electron.Display
): { bounds: Electron.Rectangle; clip: Electron.Rectangle } | null {
  if (!layout) {
    return null
  }

  const toPhysical = (rect: Electron.Rectangle): Electron.Rectangle => {
    const x = Math.round(rect.x)
    const y = Math.round(rect.y)

    return screen.dipToScreenRect(win, {
      x: display.bounds.x + x,
      y: display.bounds.y + y,
      width: Math.max(1, Math.round(rect.x + rect.width) - x),
      height: Math.max(1, Math.round(rect.y + rect.height) - y)
    })
  }

  return {
    bounds: toPhysical(layout.bounds),
    clip: toPhysical({
      x: Math.max(0, layout.clip.x),
      y: Math.max(0, layout.clip.y),
      width: Math.min(display.bounds.width, layout.clip.x + layout.clip.width) - Math.max(0, layout.clip.x),
      height: Math.min(display.bounds.height, layout.clip.y + layout.clip.height) - Math.max(0, layout.clip.y)
    })
  }
}

export function systemTrayFromNative(
  win: Electron.BrowserWindow,
  display: Electron.Display,
  physical: DesktopHostSystemTray | null
): PresentationState['systemTray'] {
  if (!physical) {
    return null
  }

  const bounds = display.bounds
  const monitor = screen.dipToScreenRect(win, bounds)
  const rect = physical.bounds

  if (
    rect.x < monitor.x - 2 ||
    rect.y < monitor.y - 2 ||
    rect.x + rect.width > monitor.x + monitor.width + 2 ||
    rect.y + rect.height > monitor.y + monitor.height + 2
  ) {
    throw new Error('系统托盘位置与交互屏不匹配，已恢复窗口模式。')
  }

  const local = screen.screenToDipRect(win, rect)
  const localX = Math.max(0, local.x - bounds.x)
  const localY = Math.max(0, local.y - bounds.y)
  const localRight = Math.min(bounds.width, local.x + local.width - bounds.x)
  const localBottom = Math.min(bounds.height, local.y + local.height - bounds.y)

  if (localRight <= localX || localBottom <= localY) {
    throw new Error('无法确认系统托盘位置，已恢复窗口模式。')
  }

  if (!getDesktopRailLayout(display.bounds.width).fits) {
    throw new Error('屏幕没有足够空间显示桌面底栏，已恢复窗口模式。')
  }

  return { width: localRight - localX, height: localBottom - localY, visible: physical.visible }
}
