import { BrowserWindow, screen } from 'electron'

import type { PresentationDisplay } from '@ipc/contracts'

import { DESKTOP_LAYOUT, getDesktopRailLayout } from '../../../shared/desktop-layout'

export interface DisplayResolution {
  geometry: string
  width: number
  height: number
}

export function displayGeometry(display: Electron.Display): string {
  return JSON.stringify([display.bounds, display.scaleFactor, display.rotation])
}

export function sameDisplayGeometry(left: Electron.Display, right: Electron.Display): boolean {
  return (
    left.id === right.id &&
    left.scaleFactor === right.scaleFactor &&
    left.rotation === right.rotation &&
    left.bounds.x === right.bounds.x &&
    left.bounds.y === right.bounds.y &&
    left.bounds.width === right.bounds.width &&
    left.bounds.height === right.bounds.height
  )
}

/** 原生矩形按交互屏裁剪成本地坐标；完全在屏外时返回 null。 */
export function displayLocalRect(dip: Electron.Rectangle, display: Electron.Rectangle): Electron.Rectangle | null {
  const x = Math.max(0, dip.x - display.x)
  const y = Math.max(0, dip.y - display.y)
  const right = Math.min(display.width, dip.x + dip.width - display.x)
  const bottom = Math.min(display.height, dip.y + dip.height - display.y)

  if (right <= x || bottom <= y) {
    return null
  }

  return { x, y, width: right - x, height: bottom - y }
}

export function resolveDisplay(
  displays: Electron.Display[],
  preferredId: number | null,
  sender: Electron.WebContents | undefined,
  mainWindow: BrowserWindow | null
): Electron.Display | undefined {
  const preferred = displays.find(display => display.id === preferredId)

  if (preferred || !displays.length) {
    return preferred
  }

  const source = sender ? BrowserWindow.fromWebContents(sender) : mainWindow

  const currentId =
    source && !source.isDestroyed() && (sender || source.isVisible() || source.isMinimized())
      ? screen.getDisplayMatching(source.getBounds()).id
      : screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).id

  return displays.find(display => display.id === currentId) ?? displays[0]
}

export function buildDisplays(
  displays: Electron.Display[],
  primaryId: number | null,
  resolutions: ReadonlyMap<number, DisplayResolution>
): PresentationDisplay[] {
  return displays.map(display => {
    const native = resolutions.get(display.id)
    const resolution = native?.geometry === displayGeometry(display) ? native : null

    return {
      id: display.id,
      label: display.label || `显示器 ${display.id}`,
      isPrimary: display.id === primaryId,
      width: display.bounds.width,
      height: display.bounds.height,
      pixelWidth:
        process.platform === 'win32'
          ? (resolution?.width ?? null)
          : Math.round(display.bounds.width * display.scaleFactor),
      pixelHeight:
        process.platform === 'win32'
          ? (resolution?.height ?? null)
          : Math.round(display.bounds.height * display.scaleFactor)
    }
  })
}

/** 底栏需要的最小可用宽度；macOS 还要留出系统菜单栏与 Dock 的高度。 */
export function desktopSpaceFits(display: Electron.Display): boolean {
  const availableWidth =
    process.platform === 'darwin'
      ? display.bounds.width -
        2 *
          Math.max(
            display.workArea.x - display.bounds.x,
            display.bounds.x + display.bounds.width - display.workArea.x - display.workArea.width
          )
      : display.bounds.width

  return (
    getDesktopRailLayout(availableWidth).fits &&
    !(process.platform === 'darwin' && display.workArea.height <= DESKTOP_LAYOUT.bottomInset)
  )
}

export function assertNativeWorkArea(display: Electron.Rectangle, area: Electron.Rectangle): void {
  if (process.platform !== 'darwin') {
    return
  }

  const safeWidth = display.width - 2 * Math.max(area.x, display.width - (area.x + area.width))

  if (area.height <= DESKTOP_LAYOUT.bottomInset || !getDesktopRailLayout(safeWidth).fits) {
    throw new Error('系统可用区域没有足够空间显示桌面底栏，请使用窗口模式。')
  }
}
