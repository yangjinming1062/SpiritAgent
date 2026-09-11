// 入口面互斥管理器：生活空间与工作台同一时刻最多一个窗口可见。
//
// 状态写入场景：
//   - 打开入口面：互斥切到指定面（先收起另一面再展示），并将上次打开的面持久化；
//   - 关闭入口面：从进程间通信边界收起当前面，记录上次打开的面但不重写偏好；
//   - 窗口自身关闭：与从进程间通信边界收起保持一致；
//   - 水合历史入口：启动期读取偏好，回灌给调用方，渲染层首屏按此决定双击精灵去向。
//
// 互斥控制：本模块对当前展开面的串行访问用微任务队列排队，避免并发打开产生竞态。
// 消费方：主进程路由分发、托盘菜单、精灵右键与桌面双击入口。

import {
  type DesktopSurfaceBounds,
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  IPC,
  normalizeSurfaceId,
  type SurfaceId
} from '@ipc/contracts'
import { BrowserWindow, type IpcMain, type IpcMainInvokeEvent, screen } from 'electron'

import * as runnerConfigStore from '../shared/lib/runner-config-store'
import { broadcastToAllWindows } from '../shared/utils'

export interface SurfacesManager {
  closeSurface: () => Promise<void>
  getState: () => DesktopSurfaceChangedEvent
  hydrateLastSurface: () => SurfaceId
  isMaximizedSurface: () => boolean
  maximizeSurface: () => void
  minimizeSurface: () => void
  onWindowClosed: (id: SurfaceId, win: BrowserWindow) => void
  openSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  registerIpcHandlers: (deps: { ipcMain: IpcMain }) => void
  toggleSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
}

interface SurfacesManagerOptions {
  createWindow: (id: SurfaceId, payload?: DesktopSurfaceOpenPayload) => Promise<BrowserWindow>
  navigateWindow?: (win: BrowserWindow, id: SurfaceId, payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  rememberLog?: (chunk: string) => void
  syncSpriteToDisplay?: (display: Electron.Display) => void
}

const LAST_SURFACE_KEY_PATH = ['ui', 'last_surface'] as const

async function persistLastSurface(id: SurfaceId): Promise<void> {
  await runnerConfigStore.patch(LAST_SURFACE_KEY_PATH, { value: id })
}

// 上报工作台窗口相对于当前屏幕工作区的坐标
function captureBounds(
  win: BrowserWindow,
  syncSpriteToDisplay?: (display: Electron.Display) => void
): DesktopSurfaceBounds | null {
  if (win.isDestroyed()) {
    return null
  }

  const b = win.getBounds()
  const display = screen.getDisplayMatching(b)
  syncSpriteToDisplay?.(display)

  return {
    displayId: display.id,
    height: b.height,
    width: b.width,
    x: b.x - display.workArea.x,
    y: b.y - display.workArea.y
  }
}

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  const windows = new Map<SurfaceId, BrowserWindow>()
  const boundsUnbinders = new Map<SurfaceId, () => void>()
  let openSurfaceId: null | SurfaceId = null
  let pendingChain: Promise<unknown> = Promise.resolve()
  let lastSurface: SurfaceId = 'living'
  let lastSurfaceHydrated = false

  function log(chunk: string): void {
    options.rememberLog?.(chunk)
  }

  function snapshot(): DesktopSurfaceChangedEvent {
    const open = openSurfaceId
    // 栖息目标坐标仅在工作台开窗时下发，生活空间不携带
    const win = open === 'workbench' ? windows.get(open) : null

    return {
      bounds: open === 'workbench' && win ? captureBounds(win, options.syncSpriteToDisplay) : null,
      lastSurface,
      open
    }
  }

  function bindBoundsReporting(id: SurfaceId, win: BrowserWindow): void {
    // 同一窗口被复用时先解绑，避免重复监听；createWindow 路径只触发一次。
    boundsUnbinders.get(id)?.()

    let reporterRaf: ReturnType<typeof setTimeout> | null = null
    let hasPendingMove = false
    let lastBroadcastBounds: DesktopSurfaceBounds | null | undefined = undefined

    const rebroadcast = (): void => {
      if (openSurfaceId !== id) {
        return
      }

      const snap = snapshot()
      const b = snap.bounds

      const isBoundsEqual =
        lastBroadcastBounds === b ||
        (Boolean(lastBroadcastBounds && b) &&
          lastBroadcastBounds!.x === b!.x &&
          lastBroadcastBounds!.y === b!.y &&
          lastBroadcastBounds!.width === b!.width &&
          lastBroadcastBounds!.height === b!.height &&
          lastBroadcastBounds!.displayId === b!.displayId)

      if (lastBroadcastBounds !== undefined && isBoundsEqual) {
        return
      }

      lastBroadcastBounds = b
      broadcastToAllWindows(IPC.event.surfaceChanged, snap)
    }

    const onChange = (): void => {
      if (reporterRaf !== null) {
        hasPendingMove = true

        return
      }

      rebroadcast()

      reporterRaf = setTimeout(() => {
        reporterRaf = null

        if (hasPendingMove) {
          hasPendingMove = false
          rebroadcast()
        }
      }, 16)
    }

    win.on('move', onChange)
    win.on('resize', onChange)
    win.on('show', rebroadcast)

    boundsUnbinders.set(id, () => {
      if (reporterRaf !== null) {
        clearTimeout(reporterRaf)
        reporterRaf = null
      }

      hasPendingMove = false
      lastBroadcastBounds = undefined
      win.off('move', onChange)
      win.off('resize', onChange)
      win.off('show', rebroadcast)
    })
  }

  function withMutex<T>(task: () => Promise<T>): Promise<T> {
    const next = pendingChain.then(task, task)
    pendingChain = next.catch(() => {})

    return next
  }

  const onWindowClosed = (id: SurfaceId, win: BrowserWindow): void => {
    if (windows.get(id) !== win) {
      return
    }

    windows.delete(id)
    boundsUnbinders.get(id)?.()
    boundsUnbinders.delete(id)

    if (openSurfaceId === id) {
      openSurfaceId = null
      broadcastToAllWindows(IPC.event.surfaceChanged, snapshot())
    }
  }

  const internalClose = (): void => {
    if (!openSurfaceId) {
      return
    }

    const id = openSurfaceId
    const win = windows.get(id)

    if (win && !win.isDestroyed()) {
      win.hide()
    }

    openSurfaceId = null
    broadcastToAllWindows(IPC.event.surfaceChanged, snapshot())
  }

  const internalOpen = async (payload: DesktopSurfaceOpenPayload): Promise<void> => {
    const id = normalizeSurfaceId(payload.surface)
    const previous = openSurfaceId

    if (previous && previous !== id) {
      const prevWin = windows.get(previous)

      if (prevWin && !prevWin.isDestroyed()) {
        prevWin.hide()
      }

      openSurfaceId = null
    }

    let win = windows.get(id)

    if (!win || win.isDestroyed()) {
      win = await options.createWindow(id, payload)
      windows.set(id, win)
      bindBoundsReporting(id, win)
    } else if (payload.view || payload.sessionId) {
      await options.navigateWindow?.(win, id, payload)
    }

    if (win.isMinimized()) {
      win.restore()
    }

    win.show()
    win.focus()
    openSurfaceId = id
    lastSurface = id
    lastSurfaceHydrated = true

    broadcastToAllWindows(IPC.event.surfaceChanged, snapshot())
    await persistLastSurface(id)
  }

  const openSurface = async (payload: DesktopSurfaceOpenPayload): Promise<void> => {
    await withMutex(async () => {
      await internalOpen(payload)
    })
  }

  const toggleSurface = async (payload: DesktopSurfaceOpenPayload): Promise<void> => {
    const id = normalizeSurfaceId(payload.surface)

    await withMutex(async () => {
      if (openSurfaceId === id) {
        const win = windows.get(id)

        if (win && !win.isDestroyed() && win.isVisible() && !win.isMinimized()) {
          internalClose()

          return
        }
      }

      await internalOpen(payload)
    })
  }

  const closeSurface = async (): Promise<void> => {
    await withMutex(async () => {
      internalClose()
    })
  }

  const getState = (): DesktopSurfaceChangedEvent => snapshot()

  const hydrateLastSurface = (): SurfaceId => {
    if (lastSurfaceHydrated) {
      return lastSurface
    }

    const ui = runnerConfigStore.read().ui as { last_surface?: unknown } | undefined
    const id = normalizeSurfaceId(ui?.last_surface)
    lastSurface = id
    lastSurfaceHydrated = true

    return id
  }

  const minimizeSurface = (): void => {
    if (!openSurfaceId) {
      return
    }

    const win = windows.get(openSurfaceId)

    if (win && !win.isDestroyed()) {
      win.minimize()
    }
  }

  const maximizeSurface = (): void => {
    if (!openSurfaceId) {
      return
    }

    const win = windows.get(openSurfaceId)

    if (win && !win.isDestroyed()) {
      if (win.isMaximized()) {
        win.unmaximize()
      } else {
        win.maximize()
      }
    }
  }

  const isMaximizedSurface = (): boolean => {
    if (!openSurfaceId) {
      return false
    }

    const win = windows.get(openSurfaceId)

    return Boolean(win && !win.isDestroyed() && win.isMaximized())
  }

  const registerIpcHandlers = ({ ipcMain }: { ipcMain: IpcMain }): void => {
    ipcMain.handle(IPC.invoke.surfaceOpen, (_event, payload: unknown) => {
      const surface = (payload as { surface?: unknown } | null)?.surface
      const view = (payload as { view?: unknown } | null)?.view
      const sessionId = (payload as { sessionId?: unknown } | null)?.sessionId

      return openSurface({
        sessionId: typeof sessionId === 'string' ? sessionId : undefined,
        surface: normalizeSurfaceId(surface),
        view: typeof view === 'string' ? view : undefined
      })
    })

    ipcMain.handle(IPC.invoke.surfaceToggle, (_event, payload: unknown) => {
      const surface = (payload as { surface?: unknown } | null)?.surface
      const view = (payload as { view?: unknown } | null)?.view
      const sessionId = (payload as { sessionId?: unknown } | null)?.sessionId

      return toggleSurface({
        sessionId: typeof sessionId === 'string' ? sessionId : undefined,
        surface: normalizeSurfaceId(surface),
        view: typeof view === 'string' ? view : undefined
      })
    })

    const resolveWindow = (event: IpcMainInvokeEvent): BrowserWindow | null => {
      const fromSender = BrowserWindow.fromWebContents(event.sender)

      if (fromSender && !fromSender.isDestroyed()) {
        return fromSender
      }

      if (openSurfaceId) {
        const current = windows.get(openSurfaceId)

        if (current && !current.isDestroyed()) {
          return current
        }
      }

      return null
    }

    ipcMain.handle(IPC.invoke.surfaceClose, () => closeSurface())
    ipcMain.handle(IPC.invoke.surfaceFocus, () => {
      if (!openSurfaceId) {
        return
      }

      const win = windows.get(openSurfaceId)

      if (win && !win.isDestroyed()) {
        if (win.isMinimized()) {
          win.restore()
        }

        win.show()
        win.focus()
      }
    })
    ipcMain.handle(IPC.invoke.surfaceMinimize, event => {
      resolveWindow(event)?.minimize()
    })
    ipcMain.handle(IPC.invoke.surfaceMaximize, event => {
      const win = resolveWindow(event)

      if (win) {
        if (win.isMaximized()) {
          win.unmaximize()
        } else {
          win.maximize()
        }
      }
    })
    ipcMain.handle(IPC.invoke.surfaceIsMaximized, event => {
      return Boolean(resolveWindow(event)?.isMaximized())
    })
    ipcMain.handle(
      IPC.invoke.surfaceSetIgnoreMouseEvents,
      (event, payload?: { forward?: boolean; ignore: boolean }) => {
        const win = resolveWindow(event)

        if (win && !win.isDestroyed()) {
          const ignore = Boolean(payload?.ignore)
          win.setIgnoreMouseEvents(ignore, { forward: ignore && payload?.forward !== false })
        }
      }
    )
    ipcMain.handle(IPC.invoke.surfaceGetState, () => snapshot())
  }

  log('[surfaces] manager ready')

  return {
    closeSurface,
    getState,
    hydrateLastSurface,
    isMaximizedSurface,
    maximizeSurface,
    minimizeSurface,
    onWindowClosed,
    openSurface,
    registerIpcHandlers,
    toggleSurface
  }
}
