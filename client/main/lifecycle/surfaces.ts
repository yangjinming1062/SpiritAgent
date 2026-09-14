// 入口面互斥管理器：生活空间与工作台同一时刻最多一个窗口可见。
//
// 状态写入场景：
//   - 打开入口面：互斥切到指定面（先收起另一面再展示），并将上次打开的面持久化；
//   - 关闭入口面：从进程间通信边界收起当前面，记录上次打开的面但不重写偏好；
//   - 窗口自身关闭：与从进程间通信边界收起保持一致；
//   - 水合历史入口：启动期读取偏好，供托盘双击决定开窗去向。
//
// 互斥控制：本模块对当前展开面的串行访问用微任务队列排队，避免并发打开产生竞态。
// 消费方：主进程路由分发、托盘点击 / 双击与精灵右键入口。

import {
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
  hydrateLastSurface: () => SurfaceId
  isMaximizedSurface: () => boolean
  isSurfaceWindow: (id: SurfaceId, win: BrowserWindow) => boolean
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

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  const windows = new Map<SurfaceId, BrowserWindow>()
  let openSurfaceId: null | SurfaceId = null
  let pendingChain: Promise<unknown> = Promise.resolve()
  let lastSurface: SurfaceId = 'living'
  let lastSurfaceHydrated = false
  let unbindWorkbenchDisplaySync: null | (() => void) = null

  function log(chunk: string): void {
    options.rememberLog?.(chunk)
  }

  function snapshot(): DesktopSurfaceChangedEvent {
    return { open: openSurfaceId }
  }

  // 工作台开启时桌面精灵窗虽隐藏，仍须跟随到同屏，确保工作台关闭后原地恢复。
  // 这是主进程窗口副作用，不属于渲染层表面状态。
  function syncSpriteToWorkbenchDisplay(win: BrowserWindow): void {
    const sync = options.syncSpriteToDisplay

    if (!sync || openSurfaceId !== 'workbench' || win.isDestroyed()) {
      return
    }

    sync(screen.getDisplayMatching(win.getBounds()))
  }

  function clearWorkbenchDisplaySync(): void {
    unbindWorkbenchDisplaySync?.()
    unbindWorkbenchDisplaySync = null
  }

  function bindWorkbenchDisplaySync(win: BrowserWindow): void {
    // 同一窗口被复用时先解绑，避免重复监听；createWindow 路径只触发一次。
    clearWorkbenchDisplaySync()

    let syncTimer: ReturnType<typeof setTimeout> | null = null
    let hasPendingChange = false

    const onChange = (): void => {
      if (syncTimer !== null) {
        hasPendingChange = true

        return
      }

      syncSpriteToWorkbenchDisplay(win)

      syncTimer = setTimeout(() => {
        syncTimer = null

        if (hasPendingChange) {
          hasPendingChange = false
          syncSpriteToWorkbenchDisplay(win)
        }
      }, 16)
    }

    win.on('move', onChange)
    win.on('resize', onChange)

    unbindWorkbenchDisplaySync = () => {
      if (syncTimer !== null) {
        clearTimeout(syncTimer)
        syncTimer = null
      }

      hasPendingChange = false
      win.off('move', onChange)
      win.off('resize', onChange)
    }
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

    if (id === 'workbench') {
      clearWorkbenchDisplaySync()
    }

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

      if (id === 'workbench') {
        bindWorkbenchDisplaySync(win)
      }
    } else if (payload.view || payload.sessionId) {
      await options.navigateWindow?.(win, id, payload)
    }

    // navigate/create 期间用户关窗：窗口已销毁，不能再 show/focus，也不能残留 openSurfaceId。
    if (win.isDestroyed()) {
      if (windows.get(id) === win) {
        windows.delete(id)

        if (id === 'workbench') {
          clearWorkbenchDisplaySync()
        }
      }

      if (openSurfaceId === id) {
        openSurfaceId = null
        broadcastToAllWindows(IPC.event.surfaceChanged, snapshot())
      }

      return
    }

    if (win.isMinimized()) {
      win.restore()
    }

    win.show()
    win.focus()
    openSurfaceId = id
    lastSurface = id
    lastSurfaceHydrated = true

    if (id === 'workbench') {
      syncSpriteToWorkbenchDisplay(win)
    }

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

  const isSurfaceWindow = (id: SurfaceId, win: BrowserWindow): boolean => {
    return windows.get(id) === win && !win.isDestroyed()
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
    hydrateLastSurface,
    isMaximizedSurface,
    isSurfaceWindow,
    maximizeSurface,
    minimizeSurface,
    onWindowClosed,
    openSurface,
    registerIpcHandlers,
    toggleSurface
  }
}
