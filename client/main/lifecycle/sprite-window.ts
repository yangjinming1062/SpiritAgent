import { type App, BrowserWindow, screen } from 'electron'

import { readRestPosition } from '../ipc/sprite'

import type { ZoomPersistence } from './zoom-persistence'

const TITLEBAR_HEIGHT = 34
const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14

const WINDOW_BUTTON_POSITION = {
  x: 24,
  y: TITLEBAR_HEIGHT / 2 - MACOS_TRAFFIC_LIGHTS_HEIGHT / 2
}

const NATIVE_OVERLAY_BUTTON_WIDTH = 144

export interface WindowState {
  isFullscreen: boolean
  nativeOverlayWidth: number
  windowButtonPosition: { x: number; y: number } | null
}

export interface SpriteWindowDeps {
  app: Pick<App, 'dock' | 'getPath' | 'isPackaged'>
  bootProgress: { broadcast: () => void }
  getAppIconPath: () => null | string
  getMainWindow: () => null | BrowserWindow
  isMac: boolean
  preloadPath: string
  rendererUrlFor: (id: 'sprite') => string
  setMainWindow: (win: null | BrowserWindow) => void
  spriteTransparent: boolean
  windowHandlers: { installStandardWindowHandlers: (win: BrowserWindow) => void }
  zoomPersistence: Pick<ZoomPersistence, 'restorePersistedZoomLevel'>
  installCloseInterceptor: (win: BrowserWindow) => void
}

function getWindowButtonPosition(
  getMainWindow: () => null | BrowserWindow,
  isMac: boolean
): null | {
  x: number
  y: number
} {
  if (!isMac) {
    return null
  }

  return getMainWindow()?.getWindowButtonPosition?.() || WINDOW_BUTTON_POSITION
}

function getNativeOverlayWidth(isMac: boolean): number {
  return isMac ? 0 : NATIVE_OVERLAY_BUTTON_WIDTH
}

export function getWindowState(deps: { getMainWindow: () => null | BrowserWindow; isMac: boolean }): WindowState {
  return {
    isFullscreen: Boolean(deps.getMainWindow()?.isFullScreen?.()),
    nativeOverlayWidth: getNativeOverlayWidth(deps.isMac),
    windowButtonPosition: getWindowButtonPosition(deps.getMainWindow, deps.isMac)
  }
}

export function createSpriteWindowFactory(deps: SpriteWindowDeps): {
  applySpriteBounds: (preferredOrigin?: { x: number; y: number }) => void
  createSpriteWindow: () => void
} {
  let boundsListenerInstalled = false

  function applySpriteBounds(preferredOrigin?: { x: number; y: number }): void {
    const mainWindow = deps.getMainWindow()

    if (!mainWindow || mainWindow.isDestroyed()) {
      return
    }

    // 贴住窗口当前覆盖的那块显示器（或启动时包含 preferredOrigin 的那块），
    // 而不是每次都弹回主显示器——精灵必须停留在用户拖到的那块显示器上。
    // 当原显示器被拔掉时，getDisplayMatching 会回退到最近的那块。
    const base = preferredOrigin
      ? { height: 1, width: 1, x: preferredOrigin.x, y: preferredOrigin.y }
      : mainWindow.getBounds()

    mainWindow.setBounds(screen.getDisplayMatching(base).workArea)
  }

  function createSpriteWindow(): void {
    const icon = deps.getAppIconPath() || undefined

    const mainWindow = new BrowserWindow({
      alwaysOnTop: true,
      backgroundColor: '#00000000',
      frame: false,
      hasShadow: false,
      height: 320,
      movable: false,
      resizable: false,
      show: false,
      skipTaskbar: true,
      // `type: 'panel'` 仅适用于 macOS（Cocoa NSPanel）；在 Win/Linux 上设置会输出 deprecation 警告。
      type: deps.isMac ? 'panel' : undefined,
      transparent: deps.spriteTransparent,
      webPreferences: {
        backgroundThrottling: false,
        contextIsolation: true,
        devTools: !deps.app.isPackaged,
        nodeIntegration: false,
        preload: deps.preloadPath,
        sandbox: true
      },
      width: 480
    })

    deps.setMainWindow(mainWindow)
    applySpriteBounds(readRestPosition(deps.app.getPath('userData'))?.origin)
    mainWindow.setIgnoreMouseEvents(true, { forward: deps.spriteTransparent })

    // macOS 用 'screen-saver' z-band（位于 floating 之上，能压过 exclusive fullscreen 游戏）；
    // Win/Linux 回退 'floating'。Windows 的 exclusive fullscreen 完全绕过 DWM，
    // 伙伴窗口无法覆盖在上面（已记录的限制）。
    if (deps.isMac) {
      mainWindow.setAlwaysOnTop(true, 'screen-saver', 1)
    } else {
      mainWindow.setAlwaysOnTop(true, 'floating')
    }

    if (deps.isMac && icon) {
      deps.app.dock?.setIcon(icon)
    }

    if (!boundsListenerInstalled) {
      boundsListenerInstalled = true
      screen.on('display-metrics-changed', () => applySpriteBounds())
    }

    deps.windowHandlers.installStandardWindowHandlers(mainWindow)
    deps.installCloseInterceptor(mainWindow)

    void mainWindow.loadURL(deps.rendererUrlFor('sprite'))
    mainWindow.webContents.once('did-finish-load', () => {
      deps.zoomPersistence.restorePersistedZoomLevel(mainWindow)
      deps.bootProgress.broadcast()
      mainWindow.showInactive()
    })
  }

  return { applySpriteBounds, createSpriteWindow }
}
