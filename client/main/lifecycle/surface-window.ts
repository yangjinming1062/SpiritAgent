import { type App, BrowserWindow, screen } from 'electron'

import { type DesktopSurfaceOpenPayload, IPC, normalizeAppNavigation } from '@ipc/contracts'
import { clamp, errorMessage } from '@runtime'

import { sendToWindow } from '../shared/utils'

import { PANEL_SIZE } from './surface-companion'
import type { SurfacesManager } from './surfaces'
import type { ZoomPersistence } from './zoom-persistence'

interface SurfaceWindowDeps {
  appName: string
  app: Pick<App, 'dock' | 'isPackaged'>
  getAppIconPath: () => null | string
  getSurfaces: () => null | SurfacesManager
  isQuitting: () => boolean
  isMac: boolean
  preloadPath: string
  rebuildTrayMenu: () => void
  rendererUrlFor: (id: 'main', theme?: string) => string
  rememberLog: (message: string) => void
  seedTheme: () => string | undefined
  windowHandlers: { installSurfaceWindowHandlers: (win: BrowserWindow) => void }
  zoomPersistence: Pick<ZoomPersistence, 'restorePersistedZoomLevel'>
}

export function createSurfaceWindowFactory(deps: SurfaceWindowDeps): {
  createSurfaceWindow: (payload?: DesktopSurfaceOpenPayload) => Promise<BrowserWindow>
  navigateSurfaceWindow: (win: BrowserWindow, payload: DesktopSurfaceOpenPayload) => void
} {
  function surfaceLoadUrl(payload?: DesktopSurfaceOpenPayload): string {
    const url = new URL(deps.rendererUrlFor('main', deps.seedTheme()))
    const target = normalizeAppNavigation(payload)

    if (target.page === 'settings' && target.section) {
      url.hash = `#/settings/${target.section}`

      if (target.settingsTab) {
        url.hash += `?subtab=${target.settingsTab}`
      }
    } else if (target.page === 'companion' && target.companionSection) {
      url.hash = `#/companion/${target.companionSection}`
    } else if (target.page) {
      url.hash = `#/${target.page}`
    }

    for (const key of ['sessionId', 'messageKey'] as const) {
      const value = target[key]

      if (value) {
        url.searchParams.set(key, value)
      }
    }

    if (target.pursuitId) {
      url.searchParams.set('pursuitId', String(target.pursuitId))
    }

    return url.toString()
  }

  async function createSurfaceWindow(payload?: DesktopSurfaceOpenPayload): Promise<BrowserWindow> {
    const defaults = PANEL_SIZE
    const icon = deps.getAppIconPath() || undefined
    const wa = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
    const width = clamp(defaults.width, Math.min(defaults.minWidth, wa.width), Math.max(1, wa.width - 16))
    const height = clamp(defaults.height, Math.min(defaults.minHeight, wa.height), Math.max(1, wa.height - 16))

    const initialBounds = {
      width,
      height,
      x: Math.round(wa.x + Math.max(0, (wa.width - width) / 2)),
      y: Math.round(wa.y + Math.max(0, (wa.height - height) / 2))
    }

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
      title: deps.appName,
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

    try {
      // 页面脚本可在 loadURL 完成前请求 IPC，构造后先登记唯一受控 sender。
      deps.getSurfaces()?.onWindowLoading(win)

      if (deps.isMac && icon) {
        deps.app.dock?.setIcon(icon)
      }

      deps.windowHandlers.installSurfaceWindowHandlers(win)

      win.on('close', event => {
        if (deps.isQuitting()) {
          return
        }

        event.preventDefault()
        void deps
          .getSurfaces()
          ?.closeSurface()
          .catch(error => {
            deps.rememberLog(`[main-window] close failed: ${errorMessage(error)}`)
          })
      })
      win.on('closed', () => {
        deps.getSurfaces()?.onWindowClosed(win)
        deps.rebuildTrayMenu()
      })

      win.on('show', () => deps.rebuildTrayMenu())
      win.on('hide', () => deps.rebuildTrayMenu())

      await win.loadURL(surfaceLoadUrl(payload))
      deps.zoomPersistence.restorePersistedZoomLevel(win)
    } catch (error) {
      deps.getSurfaces()?.onWindowClosed(win)

      if (!win.isDestroyed()) {
        win.destroy()
      }

      throw error
    }

    return win
  }

  function navigateSurfaceWindow(win: BrowserWindow, payload: DesktopSurfaceOpenPayload): void {
    sendToWindow(win, IPC.event.surfaceNavigate, payload)
  }

  return { createSurfaceWindow, navigateSurfaceWindow }
}
