// 入口面互斥管理器：生活空间与工作台同一时刻最多一个窗口可见。

import {
  BrowserWindow,
  type IpcMain,
  type IpcMainInvokeEvent,
  powerMonitor,
  type Rectangle,
  screen,
  type WebContents
} from 'electron'

import {
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  IPC,
  normalizeSurfaceId,
  type SurfaceCompanionPreference,
  type SurfaceCompanionState,
  type SurfaceId
} from '@ipc/contracts'
import { clamp } from '@runtime'

import { isSenderWindow } from '../security/ipc-trust'
import * as runnerConfigStore from '../shared/lib/runner-config-store'
import {
  broadcastToAllWindows,
  createPlaybackClaims,
  createSerialQueue,
  isWindowShown,
  setWindowIgnoreMouseEvents
} from '../shared/utils'

import { companionSlot, outerBounds, PANEL_SIZES, panelBounds, parseCompanionPreference } from './surface-companion'
import type { CreatedSurfaceWindow } from './surface-window'

export interface SurfacesManager {
  closeSurface: () => Promise<void>
  hydrateLastSurface: () => SurfaceId
  /** sender 是否为该入口面当前窗口的 webContents；用于只允许特定入口调用的通道。 */
  isSurfaceSender: (id: SurfaceId, sender: Pick<WebContents, 'id'>) => boolean
  minimizeWindow: (win: BrowserWindow) => void
  onWindowClosed: (id: SurfaceId, win: BrowserWindow) => void
  openSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  /** 精灵窗显示、隐藏、最小化或还原后调用，向各窗口发布含 `spriteVisible` 的新快照。 */
  publishSpriteVisibility: () => void
  registerIpcHandlers: (deps: { ipcMain: IpcMain }) => void
  resetPlaybackClaims: () => void
  toggleMaximizeWindow: (win: BrowserWindow) => void
  toggleSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  /** 跟随锁屏与显示器变化，须在 app ready 后调用一次。 */
  watchSystemEvents: () => void
}

interface SurfacesManagerOptions {
  createWindow: (id: SurfaceId, payload?: DesktopSurfaceOpenPayload) => Promise<CreatedSurfaceWindow>
  getCompanionPreference: (id: SurfaceId) => SurfaceCompanionPreference
  getSpriteWindow: () => BrowserWindow | null
  routeToDesktop?: (payload: DesktopSurfaceOpenPayload) => boolean
  navigateWindow?: (win: BrowserWindow, id: SurfaceId, payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  saveCompanionPreference: (id: SurfaceId, preference: SurfaceCompanionPreference) => Promise<void>
  rememberLog?: (chunk: string) => void
  syncSpriteToDisplay?: (display: Electron.Display) => void
}

const LAST_SURFACE_KEY_PATH = ['ui', 'last_surface'] as const
const RESTORE_SETTLE_MS = 60

interface SurfaceWindowState extends CreatedSurfaceWindow {
  id: SurfaceId
  side: SurfaceCompanionPreference['side']
  reason: 'edge' | null
  expectedBounds: Rectangle | null
  preservedPanel: Rectangle | null
  transitioning: boolean
  minimizing: boolean
  adjustmentTimer?: ReturnType<typeof setTimeout>
  geometryTimer?: ReturnType<typeof setTimeout>
  restoreTimer?: ReturnType<typeof setTimeout>
}

function clearLayoutTimers(layout: SurfaceWindowState): void {
  clearTimeout(layout.adjustmentTimer)
  clearTimeout(layout.geometryTimer)
  clearTimeout(layout.restoreTimer)
}

async function persistLastSurface(id: SurfaceId): Promise<void> {
  await runnerConfigStore.patch(LAST_SURFACE_KEY_PATH, { value: id })
}

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  const windows = new Map<SurfaceId, SurfaceWindowState>()
  let openSurfaceId: null | SurfaceId = null
  const withMutex = createSerialQueue()
  let lastSurface: SurfaceId | null = null
  let unbindSurfaceDisplaySync: null | (() => void) = null
  let screenLocked = false
  let stateRevision = 0
  let lastPublishedState = ''

  const playbackClaims = createPlaybackClaims()

  function findSurfaceWindow(win: BrowserWindow | null): SurfaceWindowState | undefined {
    for (const surface of windows.values()) {
      if (surface.win === win) {
        return surface
      }
    }
  }

  function isCurrentWindow(surface: SurfaceWindowState): boolean {
    return windows.get(surface.id) === surface && !surface.win.isDestroyed()
  }

  function companionState(id: SurfaceId): SurfaceCompanionState {
    const preference = options.getCompanionPreference(id)
    const layout = windows.get(id)
    const win = layout?.win
    const isOpen = openSurfaceId === id && !!win && !win.isDestroyed()
    const isActive = isOpen && win.isVisible()
    const minimized = isOpen && (win.isMinimized() || !!layout?.minimizing)
    const maximized = isActive && (win.isMaximized() || !!layout?.transitioning)

    const hiddenReason = !preference.enabled
      ? null
      : maximized
        ? 'maximized'
        : minimized
          ? 'minimized'
          : !isActive
            ? 'window-hidden'
            : screenLocked
              ? 'screen-locked'
              : (layout?.reason ?? null)

    const slotWidth = maximized ? 0 : (layout?.slotWidth ?? 0)

    return {
      hiddenReason,
      preference,
      slotWidth,
      outerWidth: win && !win.isDestroyed() ? win.getBounds().width : 0,
      visible: preference.enabled && hiddenReason === null && slotWidth > 0
    }
  }

  function spriteWindowVisible(): boolean {
    return isWindowShown(options.getSpriteWindow())
  }

  function snapshot(): DesktopSurfaceChangedEvent {
    const openWindow = openSurfaceId ? windows.get(openSurfaceId)?.win : null

    return {
      companions: { living: companionState('living'), workbench: companionState('workbench') },
      open: openSurfaceId,
      openVisible: isWindowShown(openWindow),
      revision: stateRevision,
      screenLocked,
      spriteVisible: spriteWindowVisible()
    }
  }

  function publish(force = false): void {
    const state = snapshot()
    const signature = JSON.stringify({ ...state, revision: 0 })

    if (!force && signature === lastPublishedState) {
      return
    }

    if (signature !== lastPublishedState) {
      lastPublishedState = signature
      stateRevision += 1
    }

    state.revision = stateRevision
    broadcastToAllWindows(IPC.event.surfaceChanged, state)
  }

  function sameBounds(a: Rectangle, b: Rectangle): boolean {
    return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height
  }

  function applyCompanionLayout(layout: SurfaceWindowState): void {
    const { id, win } = layout

    if (!isCurrentWindow(layout)) {
      return
    }

    if (win.isMaximized() || win.isMinimized() || layout.transitioning || layout.minimizing) {
      publish()

      return
    }

    const preference = options.getCompanionPreference(id)
    const area = screen.getDisplayMatching(layout.panel).workArea
    const slot = companionSlot(layout.panel, preference.side, preference.enabled, area)
    const target = outerBounds(layout.panel, preference.side, slot.width)
    layout.slotWidth = slot.width
    layout.side = preference.side
    layout.reason = slot.reason

    if (!sameBounds(win.getBounds(), target)) {
      // 收起侧栏时先放宽最小宽度；展开后再约束整个物理窗口。
      layout.expectedBounds = target
      win.setMinimumSize(PANEL_SIZES[id].minWidth, PANEL_SIZES[id].minHeight)
      win.setBounds(target)

      clearTimeout(layout.adjustmentTimer)

      layout.adjustmentTimer = setTimeout(() => {
        if (!isCurrentWindow(layout) || layout.expectedBounds !== target) {
          return
        }

        layout.adjustmentTimer = undefined
        layout.expectedBounds = null

        if (!sameBounds(win.getBounds(), target)) {
          layout.panel = panelBounds(win.getBounds(), layout.side, layout.slotWidth)
          applyCompanionLayout(layout)
        }
      }, 0)
    }

    win.setMinimumSize(PANEL_SIZES[id].minWidth + slot.width, PANEL_SIZES[id].minHeight)

    publish()
  }

  function registerSurfaceWindow(id: SurfaceId, created: CreatedSurfaceWindow): SurfaceWindowState {
    const { win } = created
    const preference = options.getCompanionPreference(id)

    const layout: SurfaceWindowState = {
      ...created,
      id,
      expectedBounds: null,
      preservedPanel: null,
      reason: null,
      side: preference.side,
      transitioning: false,
      minimizing: false
    }

    windows.set(id, layout)

    const onGeometry = (): void => {
      if (!isCurrentWindow(layout) || layout.expectedBounds) {
        return
      }

      clearTimeout(layout.geometryTimer)

      // 原生最大化事件可能排在 resize 后面，等本轮事件结束再认定为用户调整。
      layout.geometryTimer = setTimeout(() => {
        layout.geometryTimer = undefined

        if (
          !isCurrentWindow(layout) ||
          win.isMaximized() ||
          win.isMinimized() ||
          layout.transitioning ||
          layout.expectedBounds
        ) {
          return
        }

        layout.panel = panelBounds(win.getBounds(), layout.side, layout.slotWidth)
        applyCompanionLayout(layout)
      }, 0)
    }

    win.on('move', onGeometry)
    win.on('resize', onGeometry)
    win.on('maximize', () => {
      if (!isCurrentWindow(layout)) {
        return
      }

      clearTimeout(layout.restoreTimer)
      layout.restoreTimer = undefined

      layout.preservedPanel ??= panelBounds(win.getNormalBounds(), layout.side, layout.slotWidth)
      layout.panel = layout.preservedPanel
      layout.transitioning = false
      publish()
    })
    win.on('unmaximize', () => {
      if (!isCurrentWindow(layout)) {
        return
      }

      layout.transitioning = true
      publish()

      clearTimeout(layout.restoreTimer)

      layout.restoreTimer = setTimeout(() => {
        layout.restoreTimer = undefined

        if (!isCurrentWindow(layout) || win.isMaximized()) {
          return
        }

        layout.panel = layout.preservedPanel ?? layout.panel
        layout.preservedPanel = null
        layout.transitioning = false
        applyCompanionLayout(layout)
      }, RESTORE_SETTLE_MS)
    })
    win.on('hide', publish)
    win.on('show', publish)
    win.on('minimize', publish)
    win.on('restore', () => {
      layout.minimizing = false
      applyCompanionLayout(layout)
    })
    applyCompanionLayout(layout)

    return layout
  }

  // 完整入口开启时桌面精灵窗虽隐藏，仍须跟随到同屏，确保关闭后原地恢复；这是主进程窗口副作用，不属于渲染层表面状态。
  function syncSpriteToSurfaceDisplay(win: BrowserWindow): void {
    const sync = options.syncSpriteToDisplay

    if (!sync || win.isDestroyed()) {
      return
    }

    sync(screen.getDisplayMatching(win.getBounds()))
  }

  function clearSurfaceDisplaySync(): void {
    unbindSurfaceDisplaySync?.()
    unbindSurfaceDisplaySync = null
  }

  function bindSurfaceDisplaySync(win: BrowserWindow): void {
    // 同一窗口被复用时先解绑，避免重复监听；createWindow 路径只触发一次。
    clearSurfaceDisplaySync()

    let syncTimer: ReturnType<typeof setTimeout> | null = null
    let hasPendingChange = false

    const onChange = (): void => {
      if (syncTimer !== null) {
        hasPendingChange = true

        return
      }

      syncSpriteToSurfaceDisplay(win)

      syncTimer = setTimeout(() => {
        syncTimer = null

        if (hasPendingChange) {
          hasPendingChange = false
          syncSpriteToSurfaceDisplay(win)
        }
      }, 16)
    }

    win.on('move', onChange)
    win.on('resize', onChange)

    unbindSurfaceDisplaySync = () => {
      if (syncTimer !== null) {
        clearTimeout(syncTimer)
        syncTimer = null
      }

      hasPendingChange = false
      win.off('move', onChange)
      win.off('resize', onChange)
    }
  }

  const onWindowClosed = (id: SurfaceId, win: BrowserWindow): void => {
    const layout = windows.get(id)

    if (layout?.win !== win) {
      return
    }

    clearLayoutTimers(layout)
    windows.delete(id)

    if (openSurfaceId === id) {
      clearSurfaceDisplaySync()
      openSurfaceId = null
      publish()
    }
  }

  // 隐藏已打开的入口窗并清除打开状态，不发布快照。
  const hideOpenSurface = (current: SurfaceId): void => {
    const win = windows.get(current)?.win

    if (win && !win.isDestroyed()) {
      win.hide()
    }

    clearSurfaceDisplaySync()
    openSurfaceId = null
  }

  const internalClose = (): void => {
    if (openSurfaceId) {
      hideOpenSurface(openSurfaceId)
      publish()
    }
  }

  const internalOpen = async (payload: DesktopSurfaceOpenPayload, forcePublish = false): Promise<void> => {
    if (options.routeToDesktop?.(payload)) {
      publish(forcePublish)

      return
    }

    const id = normalizeSurfaceId(payload.surface)

    if (openSurfaceId && openSurfaceId !== id) {
      hideOpenSurface(openSurfaceId)
    }

    let surface = windows.get(id)

    if (!surface || surface.win.isDestroyed()) {
      surface = registerSurfaceWindow(id, await options.createWindow(id, payload))
    } else if (payload.view || payload.sessionId) {
      await options.navigateWindow?.(surface.win, id, payload)
    }

    const { win } = surface

    // navigate/create 期间用户关窗：窗口已销毁，不能再 show/focus，也不能残留 openSurfaceId。
    if (win.isDestroyed()) {
      onWindowClosed(id, win)
      publish(forcePublish)

      return
    }

    if (win.isMinimized()) {
      win.restore()
    }

    surface.minimizing = false

    win.show()
    win.focus()
    openSurfaceId = id
    bindSurfaceDisplaySync(win)
    lastSurface = id

    syncSpriteToSurfaceDisplay(win)

    publish(forcePublish)
    await persistLastSurface(id)
  }

  const openSurface = (payload: DesktopSurfaceOpenPayload): Promise<void> =>
    withMutex(async () => {
      try {
        // open 的调用方先写入本地意图，即使实际状态未变也要回灌权威快照。
        await internalOpen(payload, true)
      } catch (error) {
        publish(true)
        throw error
      }
    })

  const toggleSurface = (payload: DesktopSurfaceOpenPayload): Promise<void> => {
    const id = normalizeSurfaceId(payload.surface)

    return withMutex(async () => {
      if (options.routeToDesktop?.(payload)) {
        return
      }

      if (openSurfaceId === id && isWindowShown(windows.get(id)?.win)) {
        internalClose()

        return
      }

      await internalOpen(payload)
    })
  }

  const closeSurface = (): Promise<void> => withMutex(internalClose)

  const hydrateLastSurface = (): SurfaceId => {
    if (lastSurface === null) {
      const ui = runnerConfigStore.read().ui as { last_surface?: unknown } | undefined
      lastSurface = normalizeSurfaceId(ui?.last_surface)
    }

    return lastSurface
  }

  const minimizeWindow = (win: BrowserWindow): void => {
    if (win.isDestroyed()) {
      return
    }

    const layout = findSurfaceWindow(win)

    if (layout) {
      layout.minimizing = true
      publish()
    }

    win.minimize()
  }

  const toggleMaximizeWindow = (win: BrowserWindow): void => {
    if (win.isDestroyed()) {
      return
    }

    const layout = findSurfaceWindow(win)

    if (layout) {
      if (!win.isMaximized()) {
        layout.preservedPanel ??= { ...layout.panel }
      }

      layout.transitioning = true
      publish()
    }

    if (win.isMaximized()) {
      win.unmaximize()
    } else {
      win.maximize()
    }
  }

  const isSurfaceSender = (id: SurfaceId, sender: Pick<WebContents, 'id'>): boolean => {
    return isSenderWindow(sender, windows.get(id)?.win)
  }

  const refreshCompanionGeometry = (): void => {
    for (const layout of windows.values()) {
      const { win } = layout

      if (win.isDestroyed()) {
        continue
      }

      const area = screen.getDisplayMatching(layout.panel).workArea

      layout.panel = {
        ...layout.panel,
        x: clamp(layout.panel.x, area.x, area.x + area.width - layout.panel.width),
        y: clamp(layout.panel.y, area.y, area.y + area.height - layout.panel.height)
      }

      if (layout.preservedPanel) {
        layout.preservedPanel = { ...layout.panel }
      }

      applyCompanionLayout(layout)
    }
  }

  const setScreenLocked = (locked: boolean): void => {
    screenLocked = locked
    publish()
  }

  const watchSystemEvents = (): void => {
    const syncScreenLocked = (): void => setScreenLocked(powerMonitor.getSystemIdleState(1) === 'locked')

    syncScreenLocked()
    powerMonitor.on('lock-screen', () => setScreenLocked(true))
    powerMonitor.on('unlock-screen', () => setScreenLocked(false))
    powerMonitor.on('resume', syncScreenLocked)
    screen.on('display-added', () => refreshCompanionGeometry())
    screen.on('display-removed', () => refreshCompanionGeometry())
    screen.on('display-metrics-changed', () => refreshCompanionGeometry())
  }

  const registerIpcHandlers = ({ ipcMain }: { ipcMain: IpcMain }): void => {
    ipcMain.handle(IPC.invoke.surfaceOpen, (_event, payload: unknown) => {
      const { sessionId, surface, view } = (payload ?? {}) as { sessionId?: unknown; surface?: unknown; view?: unknown }

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
        const current = windows.get(openSurfaceId)?.win

        if (current && !current.isDestroyed()) {
          return current
        }
      }

      return null
    }

    ipcMain.handle(IPC.invoke.surfaceClose, () => closeSurface())
    ipcMain.handle(IPC.invoke.surfaceMinimize, event => {
      const win = resolveWindow(event)

      if (win) {
        minimizeWindow(win)
      }
    })
    ipcMain.handle(IPC.invoke.surfaceMaximize, event => {
      const win = resolveWindow(event)

      if (win) {
        toggleMaximizeWindow(win)
      }
    })
    ipcMain.handle(IPC.invoke.surfaceIsMaximized, event => {
      return Boolean(resolveWindow(event)?.isMaximized())
    })
    ipcMain.handle(IPC.invoke.surfaceSetIgnoreMouseEvents, (event, payload?: { forward?: boolean; ignore: boolean }) =>
      setWindowIgnoreMouseEvents(resolveWindow(event), payload)
    )
    ipcMain.handle(IPC.invoke.surfaceGetState, () => snapshot())
    ipcMain.handle(IPC.invoke.surfaceSetCompanion, (event, raw: unknown) =>
      withMutex(async () => {
        const surface = findSurfaceWindow(BrowserWindow.fromWebContents(event.sender))
        const preference = parseCompanionPreference(raw)

        if (!surface || !preference) {
          throw new Error('Invalid companion preference or surface sender')
        }

        await options.saveCompanionPreference(surface.id, preference)
        applyCompanionLayout(surface)

        return snapshot()
      })
    )
    ipcMain.handle(IPC.invoke.surfaceClaimPlay, (event, raw: unknown) => {
      if (screenLocked) {
        return false
      }

      const sender = BrowserWindow.fromWebContents(event.sender)
      const surface = findSurfaceWindow(sender)

      const allowed = surface
        ? companionState(surface.id).visible
        : sender === options.getSpriteWindow() && spriteWindowVisible() && openSurfaceId === null

      if (!allowed) {
        return false
      }

      return playbackClaims.claim(raw)
    })
  }

  options.rememberLog?.('[surfaces] manager ready')

  return {
    closeSurface,
    hydrateLastSurface,
    isSurfaceSender,
    minimizeWindow,
    onWindowClosed,
    openSurface,
    publishSpriteVisibility: publish,
    registerIpcHandlers,
    resetPlaybackClaims: playbackClaims.reset,
    toggleMaximizeWindow,
    toggleSurface,
    watchSystemEvents
  }
}
