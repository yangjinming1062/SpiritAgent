import { app, BrowserWindow, type WebContents } from 'electron'

import { isSenderWindow } from '../security/ipc-trust'

export type DesktopWindowRole = 'desktop' | 'desktop-background'

interface DesktopWindowsOptions {
  preloadPath: string
  backgroundPreloadPath: string
  rendererUrlFor: (role: DesktopWindowRole, theme?: string) => string
  seedTheme: () => string | undefined
  installWindowHandlers: (win: BrowserWindow, options?: { reloadOnCrash?: boolean }) => void
  lockZoom: (win: BrowserWindow) => void
  onFailure: (reason: string) => void
}

// 桌面窗口、原生边界与页面就绪等待使用同一所有者，模式协调方先恢复系统再销毁。
export function createDesktopWindows(options: DesktopWindowsOptions) {
  let interactive: BrowserWindow | null = null
  const backgrounds = new Set<BrowserWindow>()
  const displayBoundsByWindow = new WeakMap<BrowserWindow, Electron.Rectangle>()
  const readyWindows = new Map<BrowserWindow, { resolve: () => void; reject: (error: Error) => void }>()

  function makeWindow(display: Electron.Display, role: 'desktop' | 'desktop-background'): BrowserWindow {
    const primary = role !== 'desktop-background'

    const win = new BrowserWindow({
      ...display.bounds,
      frame: false,
      thickFrame: false,
      show: false,
      skipTaskbar: true,
      resizable: false,
      fullscreenable: false,
      hasShadow: false,
      backgroundColor: primary ? '#00000000' : '#111827',
      transparent: primary,
      title: role === 'desktop' ? '唤生桌面' : '唤生背景',
      webPreferences: {
        preload: primary ? options.preloadPath : options.backgroundPreloadPath,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        zoomFactor: 1,
        devTools: !app.isPackaged
      }
    })

    // 初始化处理器失败时，恢复方也必须能找到并销毁已创建的原生窗口。
    if (primary) {
      interactive = win
    } else {
      backgrounds.add(win)
    }

    options.lockZoom(win)

    win.setBounds(display.bounds)
    displayBoundsByWindow.set(win, { ...display.bounds })

    win.setIgnoreMouseEvents(true, primary ? { forward: true } : undefined)

    win.webContents.on('render-process-gone', (_event, details) =>
      options.onFailure(`桌面渲染进程已退出：${details.reason}`)
    )
    win.on('unresponsive', () => options.onFailure('桌面暂时无响应，已恢复系统桌面。'))

    if (primary) {
      // 桌面崩溃保持崩溃态直至 leave 销毁，不走共享 reload 恢复。
      options.installWindowHandlers(win, { reloadOnCrash: false })
      const expected = new URL(options.rendererUrlFor(role))

      const guardNavigation = (event: Electron.Event, url: string): void => {
        try {
          const target = new URL(url)

          if (
            target.protocol === expected.protocol &&
            target.host === expected.host &&
            target.pathname === expected.pathname
          ) {
            return
          }
        } catch {
          /* 不可信导航不能继承桌面能力。 */
        }

        event.preventDefault()
      }

      win.webContents.on('will-navigate', guardNavigation)
      win.webContents.on('will-redirect', guardNavigation)
    } else {
      win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
      win.webContents.on('will-navigate', event => event.preventDefault())
    }

    win.on('closed', () => options.onFailure('桌面窗口已关闭，已恢复系统桌面。'))

    return win
  }

  async function loadWindow(win: BrowserWindow, role: DesktopWindowRole): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined

    const ready = new Promise<void>((resolve, reject) => {
      readyWindows.set(win, { resolve, reject })
      timer = setTimeout(() => reject(new Error('桌面界面准备超时。')), 15000)
    })

    try {
      await Promise.all([ready, win.loadURL(options.rendererUrlFor(role, options.seedTheme()))])
    } finally {
      clearTimeout(timer)
      readyWindows.delete(win)
    }
  }

  function invalidateReady(reason: string): void {
    for (const waiting of readyWindows.values()) {
      waiting.reject(new Error(reason))
    }
  }

  function destroy(): void {
    invalidateReady('桌面窗口已释放。')
    readyWindows.clear()

    for (const win of backgrounds) {
      if (!win.isDestroyed()) {
        win.destroy()
      }
    }

    backgrounds.clear()

    if (interactive && !interactive.isDestroyed()) {
      interactive.destroy()
    }

    interactive = null
  }

  return {
    getInteractive: () => interactive,
    getBackgrounds: () => backgrounds.values(),
    getBounds: (win: BrowserWindow) => displayBoundsByWindow.get(win),
    isDesktopSender: (sender: Pick<WebContents, 'id'>) => isSenderWindow(sender, interactive),
    getBackgroundWindow: (sender: Pick<WebContents, 'id'>) => [...backgrounds].find(win => isSenderWindow(sender, win)),
    createInteractive: (display: Electron.Display) => makeWindow(display, 'desktop'),
    createBackground: (display: Electron.Display) => makeWindow(display, 'desktop-background'),
    load: loadWindow,
    reportReady: (win: BrowserWindow) => readyWindows.get(win)?.resolve(),
    invalidateReady,
    destroy
  }
}
