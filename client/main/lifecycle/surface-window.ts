import type { DesktopSurfaceOpenPayload, SurfaceCompanionPreference, SurfaceId } from '@ipc/contracts'
import { clamp } from '@runtime'
import { type App, BrowserWindow, screen } from 'electron'

import {
  companionSlotMinimum,
  companionSlotWidth,
  outerBounds,
  PANEL_SIZES,
  preferredCompanionWidth
} from './surface-companion'
import type { SurfacesManager } from './surfaces'
import type { ZoomPersistence } from './zoom-persistence'

export interface CreatedSurfaceWindow {
  panel: Electron.Rectangle
  slotWidth: number
  win: BrowserWindow
}

interface SurfaceWindowDeps {
  appName: string
  app: Pick<App, 'dock' | 'isPackaged'>
  getAppIconPath: () => null | string
  getSurfaces: () => null | SurfacesManager
  getCompanionPreference: (id: SurfaceId) => SurfaceCompanionPreference
  isMac: boolean
  preloadPath: string
  rebuildTrayMenu: () => void
  rendererUrlFor: (id: SurfaceId, theme?: string) => string
  seedTheme: () => string | undefined
  windowHandlers: { installSurfaceWindowHandlers: (win: BrowserWindow) => void }
  zoomPersistence: Pick<ZoomPersistence, 'restorePersistedZoomLevel'>
}

export function createSurfaceWindowFactory(deps: SurfaceWindowDeps): {
  createSurfaceWindow: (id: SurfaceId, payload?: DesktopSurfaceOpenPayload) => Promise<CreatedSurfaceWindow>
  navigateSurfaceWindow: (win: BrowserWindow, id: SurfaceId, payload: DesktopSurfaceOpenPayload) => Promise<void>
} {
  function surfaceLoadUrl(id: SurfaceId, payload?: DesktopSurfaceOpenPayload): string {
    const url = new URL(deps.rendererUrlFor(id, deps.seedTheme()))

    if (payload?.view) {
      url.hash = `#/${payload.view}`
    }

    if (payload?.sessionId) {
      url.searchParams.set('sessionId', payload.sessionId)
    }

    return url.toString()
  }

  async function createSurfaceWindow(
    id: SurfaceId,
    payload?: DesktopSurfaceOpenPayload
  ): Promise<CreatedSurfaceWindow> {
    const defaults = PANEL_SIZES[id]
    const icon = deps.getAppIconPath() || undefined
    const preference = deps.getCompanionPreference(id)
    const wa = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
    const initialHeight = clamp(defaults.height, defaults.minHeight, wa.height - 16)
    const desiredSlot = preference.enabled ? preferredCompanionWidth(initialHeight) : 0
    const minSlot = companionSlotMinimum(desiredSlot)
    const panelWidth = clamp(defaults.width, defaults.minWidth, wa.width - 16 - minSlot)
    const availableSlot = Math.max(0, wa.width - 16 - panelWidth)
    const slotWidth = companionSlotWidth(desiredSlot, availableSlot)
    const outerWidth = panelWidth + slotWidth

    const panel = {
      height: initialHeight,
      width: panelWidth,
      x: Math.round(wa.x + Math.max(0, (wa.width - outerWidth) / 2) + (preference.side === 'left' ? slotWidth : 0)),
      y: Math.round(wa.y + Math.max(0, (wa.height - initialHeight) / 2))
    }

    const initialBounds = outerBounds(panel, preference.side, slotWidth)

    // 入口窗用 CSS 大圆角液态玻璃；Windows 亚克力与系统阴影按 HWND 矩形铺底会在圆角四角漏灰，故关掉原生材质/圆角/阴影保持真透明。
    const win = new BrowserWindow({
      backgroundColor: '#00000000',
      frame: false,
      hasShadow: false,
      // Windows 任务栏/Alt-Tab 取窗口 icon；不传会退回 electron.exe 自带图标。
      icon,
      height: initialBounds.height,
      minHeight: defaults.minHeight,
      minWidth: defaults.minWidth,
      resizable: true,
      roundedCorners: false,
      show: false,
      skipTaskbar: false,
      title: id === 'living' ? `${deps.appName} · 生活空间` : `${deps.appName} · 工作台`,
      transparent: true,
      webPreferences: {
        backgroundThrottling: false,
        contextIsolation: true,
        devTools: !deps.app.isPackaged,
        nodeIntegration: false,
        preload: deps.preloadPath,
        sandbox: true
      },
      width: initialBounds.width,
      x: initialBounds.x,
      y: initialBounds.y
    })

    if (deps.isMac && icon) {
      deps.app.dock?.setIcon(icon)
    }

    deps.windowHandlers.installSurfaceWindowHandlers(win)

    win.on('close', () => {
      deps.getSurfaces()?.onWindowClosed(id, win)
      deps.rebuildTrayMenu()
    })

    win.on('show', () => deps.rebuildTrayMenu())
    win.on('hide', () => deps.rebuildTrayMenu())

    try {
      await win.loadURL(surfaceLoadUrl(id, payload))
    } catch (error) {
      // 构造与登记进 surfaces Map 之间的空窗：失败时回收，避免隐藏泄漏窗。
      if (!win.isDestroyed()) {
        win.destroy()
      }

      throw error
    }

    deps.zoomPersistence.restorePersistedZoomLevel(win)

    return { panel, slotWidth, win }
  }

  async function navigateSurfaceWindow(
    win: BrowserWindow,
    id: SurfaceId,
    payload: DesktopSurfaceOpenPayload
  ): Promise<void> {
    await win.loadURL(surfaceLoadUrl(id, payload))
    deps.zoomPersistence.restorePersistedZoomLevel(win)
  }

  return { createSurfaceWindow, navigateSurfaceWindow }
}
