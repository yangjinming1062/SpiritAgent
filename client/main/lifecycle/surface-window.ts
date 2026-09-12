import type { DesktopSurfaceOpenPayload, SurfaceId } from '@ipc/contracts'
import { type App, BrowserWindow, screen } from 'electron'

import type { SurfacesManager } from './surfaces'
import type { ZoomPersistence } from './zoom-persistence'

// 入口面互斥窗口工厂：living 是生活空间，workbench 是工作台。
// 形态按 SpiritAgent-客户端开发计划 §2.5 / §4 默认尺寸落定。
const SURFACE_DEFAULTS: Record<SurfaceId, { height: number; minHeight: number; minWidth: number; width: number }> = {
  living: { height: 720, minHeight: 560, minWidth: 880, width: 1080 },
  workbench: { height: 800, minHeight: 640, minWidth: 1264, width: 1532 }
}

export interface SurfaceWindowDeps {
  appName: string
  app: Pick<App, 'dock' | 'isPackaged'>
  getAppIconPath: () => null | string
  getSurfaces: () => null | SurfacesManager
  isMac: boolean
  preloadPath: string
  rebuildTrayMenu: () => void
  rendererUrlFor: (id: SurfaceId) => string
  windowHandlers: { installSurfaceWindowHandlers: (win: BrowserWindow) => void }
  zoomPersistence: Pick<ZoomPersistence, 'restorePersistedZoomLevel'>
}

export function surfaceLoadUrl(
  rendererUrlFor: (id: SurfaceId) => string,
  id: SurfaceId,
  payload?: DesktopSurfaceOpenPayload
): string {
  const url = new URL(rendererUrlFor(id))

  if (payload?.view) {
    url.hash = `#/${payload.view}`
  }

  if (payload?.sessionId) {
    url.searchParams.set('sessionId', payload.sessionId)
  }

  return url.toString()
}

export function createSurfaceWindowFactory(deps: SurfaceWindowDeps): {
  createSurfaceWindow: (id: SurfaceId, payload?: DesktopSurfaceOpenPayload) => Promise<BrowserWindow>
  navigateSurfaceWindow: (win: BrowserWindow, id: SurfaceId, payload: DesktopSurfaceOpenPayload) => Promise<void>
} {
  async function createSurfaceWindow(id: SurfaceId, payload?: DesktopSurfaceOpenPayload): Promise<BrowserWindow> {
    const defaults = SURFACE_DEFAULTS[id]
    const icon = deps.getAppIconPath() || undefined

    let initialX: number | undefined
    let initialY: number | undefined
    let initialWidth = defaults.width
    let initialHeight = defaults.height

    if (id === 'workbench') {
      const cursor = screen.getCursorScreenPoint()
      const display = screen.getDisplayNearestPoint(cursor)
      const wa = display.workArea

      initialWidth = Math.max(defaults.minWidth, Math.min(defaults.width, wa.width - 16))
      initialHeight = Math.max(defaults.minHeight, Math.min(defaults.height, wa.height - 16))
      initialX = Math.round(wa.x + Math.max(0, (wa.width - initialWidth) / 2))
      initialY = Math.round(wa.y + Math.max(0, (wa.height - initialHeight) / 2))
    }

    // 入口窗用 CSS 大圆角液态玻璃。Windows 亚克力与系统阴影按 HWND 矩形铺底，
    // 会在圆角切出的四角漏出灰底；关掉原生材质/圆角/阴影，圆角外像素保持真透明。
    const win = new BrowserWindow({
      backgroundColor: '#00000000',
      frame: false,
      hasShadow: false,
      height: initialHeight,
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
      width: initialWidth,
      x: initialX,
      y: initialY
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
      await win.loadURL(surfaceLoadUrl(deps.rendererUrlFor, id, payload))
    } catch (error) {
      // 构造与登记进 surfaces Map 之间的空窗：失败时回收，避免隐藏泄漏窗。
      if (!win.isDestroyed()) {
        win.destroy()
      }

      throw error
    }

    deps.zoomPersistence.restorePersistedZoomLevel(win)

    return win
  }

  async function navigateSurfaceWindow(
    win: BrowserWindow,
    id: SurfaceId,
    payload: DesktopSurfaceOpenPayload
  ): Promise<void> {
    await win.loadURL(surfaceLoadUrl(deps.rendererUrlFor, id, payload))
    deps.zoomPersistence.restorePersistedZoomLevel(win)
  }

  return { createSurfaceWindow, navigateSurfaceWindow }
}
