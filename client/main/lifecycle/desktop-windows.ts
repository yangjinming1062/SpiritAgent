import { app, BrowserWindow, screen, type WebContents } from 'electron'

import { IPC } from '@ipc/contracts'

import { isSenderWindow } from '../security/ipc-trust'
import { sendToWindow } from '../shared/utils'

export type DesktopWindowRole = 'desktop' | 'desktop-background'

interface DesktopWindowsOptions {
  preloadPath: string
  backgroundPreloadPath: string
  rendererUrlFor: (role: DesktopWindowRole, theme?: string) => string
  seedTheme: () => string | undefined
  installWindowHandlers: (win: BrowserWindow, options?: { reloadOnCrash?: boolean }) => void
  lockZoom: (win: BrowserWindow) => void
  onInteraction: () => void
  onFailure: (reason: string) => void
}

// 桌面窗口、原生边界与页面就绪等待使用同一所有者，模式协调方先恢复系统再销毁。
export function createDesktopWindows(options: DesktopWindowsOptions) {
  let interactive: BrowserWindow | null = null
  const backgrounds = new Set<BrowserWindow>()
  const displayBoundsByWindow = new WeakMap<BrowserWindow, Electron.Rectangle>()
  const inputGenerations = new WeakMap<BrowserWindow, number>()
  const blurGenerations = new WeakMap<BrowserWindow, number>()
  const readyWindows = new Map<BrowserWindow, { resolve: () => void; reject: (error: Error) => void }>()

  function trackInactivePointer(win: BrowserWindow): void {
    let previous: { x: number; y: number } | undefined
    let sentAt = 0

    const sample = (): void => {
      if (win.isDestroyed() || !win.isVisible() || win.isFocused()) {
        previous = undefined

        return
      }

      try {
        const cursor = screen.getCursorScreenPoint()
        const bounds = win.getContentBounds()
        const point = { x: cursor.x - bounds.x, y: cursor.y - bounds.y }
        const now = Date.now()

        if (previous && point.x === previous.x && point.y === previous.y && now - sentAt < 100) {
          return
        }

        previous = point
        sentAt = now
        sendToWindow(win, IPC.event.presentationPointerPosition, point)
      } catch (error) {
        clearInterval(timer)
        options.onFailure(`桌面指针读取失败：${error instanceof Error ? error.message : String(error)}`)
      }
    }

    // Chromium 会过滤普通层窗口失焦后的 mousemove；只补充坐标，仍由原区域探针决定捕获。
    const timer = setInterval(sample, 16)
    timer.unref()
    win.on('blur', sample)
    win.on('show', sample)
    win.once('closed', () => clearInterval(timer))
  }

  function makeWindow(display: Electron.Display, role: 'desktop' | 'desktop-background'): BrowserWindow {
    const primary = role !== 'desktop-background'

    const win = new BrowserWindow({
      ...display.bounds,
      frame: false,
      thickFrame: false,
      show: false,
      // macOS 表面覆盖完整屏幕；交互层首击即响应，背景不取得键盘焦点。
      ...(process.platform === 'darwin'
        ? { enableLargerThanScreen: true, acceptFirstMouse: primary, focusable: primary }
        : {}),
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
      inputGenerations.set(win, 0)
      blurGenerations.set(win, 0)

      if (process.platform === 'darwin') {
        trackInactivePointer(win)
      }

      const inputInteraction = (): void => {
        inputGenerations.set(win, (inputGenerations.get(win) ?? 0) + 1)
        options.onInteraction()
      }

      win.webContents.on('before-input-event', (_event, input) => {
        if (input.type === 'keyDown') {
          inputInteraction()
        }
      })
      win.webContents.on('before-mouse-event', (_event, input) => {
        if (input.type === 'mouseDown' || input.type === 'mouseWheel') {
          inputInteraction()
        }
      })
      win.on('blur', () => {
        blurGenerations.set(win, (blurGenerations.get(win) ?? 0) + 1)
      })

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

  // 桌面表面必须覆盖交互屏，否则底栏会错开原生托盘槽位。
  function enforceDisplayBounds(win: BrowserWindow): boolean {
    const display = displayBoundsByWindow.get(win)

    if (!display || win.isDestroyed()) {
      return false
    }

    const current = win.getBounds()
    // DIP 往返会向外取整，1~2 DIP 的差异不是系统压缩，不能据此反复重设窗口。
    const tolerance = 2

    const covered =
      current.x <= display.x + tolerance &&
      current.y <= display.y + tolerance &&
      current.x + current.width >= display.x + display.width - tolerance &&
      current.y + current.height >= display.y + display.height - tolerance

    if (covered) {
      return false
    }

    win.setBounds(display)

    return true
  }

  function captureInteraction(sender: Pick<WebContents, 'id'>, requireFocusHistory = true): () => boolean {
    const win = interactive

    if (!win || !isSenderWindow(sender, win)) {
      return () => false
    }

    const inputGeneration = inputGenerations.get(win)
    const blurGeneration = blurGenerations.get(win)

    return () =>
      interactive === win &&
      isSenderWindow(sender, win) &&
      inputGenerations.get(win) === inputGeneration &&
      (!requireFocusHistory || blurGenerations.get(win) === blurGeneration)
  }

  return {
    getInteractive: () => interactive,
    getBackgrounds: () => backgrounds.values(),
    getBounds: (win: BrowserWindow) => displayBoundsByWindow.get(win),
    enforceDisplayBounds,
    isDesktopSender: (sender: Pick<WebContents, 'id'>) => isSenderWindow(sender, interactive),
    captureInteraction,
    getBackgroundWindow: (sender: Pick<WebContents, 'id'>) => [...backgrounds].find(win => isSenderWindow(sender, win)),
    createInteractive: (display: Electron.Display) => makeWindow(display, 'desktop'),
    createBackground: (display: Electron.Display) => makeWindow(display, 'desktop-background'),
    load: loadWindow,
    reportReady: (win: BrowserWindow) => readyWindows.get(win)?.resolve(),
    invalidateReady,
    destroy
  }
}
