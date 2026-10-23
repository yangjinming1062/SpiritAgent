// 主窗口生命周期、侧边伙伴几何与播放资格。

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
  normalizeAppNavigation,
  type SurfaceCompanionPreference,
  type SurfaceCompanionState
} from '@ipc/contracts'
import { clamp, errorMessage } from '@runtime'

import { isSenderWindow } from '../security/ipc-trust'
import { broadcastToAllWindows, createSerialQueue, isWindowShown, setWindowIgnoreMouseEvents } from '../shared/utils'

import { companionSlot, outerBounds, PANEL_SIZE, panelBounds, parseCompanionPreference } from './surface-companion'
import type { CreatedSurfaceWindow } from './surface-window'

export interface SurfacesManager {
  closeSurface: () => Promise<void>
  getWindow: () => BrowserWindow | null
  isOpen: () => boolean
  /** sender 是否为该入口面当前窗口的 webContents；用于只允许特定入口调用的通道。 */
  isSurfaceSender: (sender: Pick<WebContents, 'id'>) => boolean
  minimizeWindow: (win: BrowserWindow) => void
  onWindowClosed: (win: BrowserWindow) => void
  onWindowLoading: (win: BrowserWindow) => void
  openSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  restoreMainWindow: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  /** 精灵窗显示、隐藏、最小化或还原后调用，向各窗口发布含 `spriteVisible` 的新快照。 */
  publishSpriteVisibility: () => void
  registerIpcHandlers: (deps: { ipcMain: IpcMain }) => void
  toggleMaximizeWindow: (win: BrowserWindow) => void
  toggleSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  /** 跟随锁屏与显示器变化，须在 app ready 后调用一次。 */
  watchSystemEvents: () => void
}

interface SurfacesManagerOptions {
  createWindow: (payload?: DesktopSurfaceOpenPayload) => Promise<CreatedSurfaceWindow>
  getCompanionPreference: () => SurfaceCompanionPreference
  getSpriteWindow: () => BrowserWindow | null
  isQuitting: () => boolean
  isPreferencesSender?: (sender: WebContents) => boolean
  routeToDesktop?: (payload: DesktopSurfaceOpenPayload) => boolean
  prepareWindow?: (win: BrowserWindow, payload: DesktopSurfaceOpenPayload) => Promise<void>
  navigateWindow?: (win: BrowserWindow, payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  saveCompanionPreference: (preference: SurfaceCompanionPreference) => Promise<void>
  rememberLog?: (chunk: string) => void
  syncSpriteToDisplay?: (display: Electron.Display) => void
}

const RESTORE_SETTLE_MS = 60

interface SurfaceWindowState extends CreatedSurfaceWindow {
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

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  let surfaceWindow: SurfaceWindowState | null = null
  let loadingWindow: BrowserWindow | null = null
  let surfaceOpen = false
  const withMutex = createSerialQueue()
  let unbindSurfaceDisplaySync: null | (() => void) = null
  let screenLocked = false
  let stateRevision = 0
  let lastPublishedState = ''

  function findSurfaceWindow(win: BrowserWindow | null): SurfaceWindowState | undefined {
    return surfaceWindow && surfaceWindow.win === win ? surfaceWindow : undefined
  }

  function isCurrentWindow(surface: SurfaceWindowState): boolean {
    return surfaceWindow === surface && !surface.win.isDestroyed()
  }

  function companionState(): SurfaceCompanionState {
    const preference = options.getCompanionPreference()
    const layout = surfaceWindow
    const win = layout?.win
    const isOpen = surfaceOpen && !!win && !win.isDestroyed()
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
    const openWindow = surfaceOpen ? surfaceWindow?.win : null

    return {
      companion: companionState(),
      open: surfaceOpen,
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
    const { win } = layout

    if (!isCurrentWindow(layout)) {
      return
    }

    if (win.isMaximized() || win.isMinimized() || layout.transitioning || layout.minimizing) {
      publish()

      return
    }

    const preference = options.getCompanionPreference()
    const area = screen.getDisplayMatching(layout.panel).workArea
    const slot = companionSlot(layout.panel, preference.side, preference.enabled, area)
    const target = outerBounds(layout.panel, preference.side, slot.width)
    layout.slotWidth = slot.width
    layout.side = preference.side
    layout.reason = slot.reason

    if (!sameBounds(win.getBounds(), target)) {
      // 收起侧栏时先放宽最小宽度；展开后再约束整个物理窗口。
      layout.expectedBounds = target
      win.setMinimumSize(PANEL_SIZE.minWidth, PANEL_SIZE.minHeight)
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

    win.setMinimumSize(PANEL_SIZE.minWidth + slot.width, PANEL_SIZE.minHeight)

    publish()
  }

  function registerSurfaceWindow(created: CreatedSurfaceWindow): SurfaceWindowState {
    const { win } = created
    const preference = options.getCompanionPreference()

    const layout: SurfaceWindowState = {
      ...created,
      expectedBounds: null,
      preservedPanel: null,
      reason: null,
      side: preference.side,
      transitioning: false,
      minimizing: false
    }

    surfaceWindow = layout

    if (loadingWindow === win) {
      loadingWindow = null
    }

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

  const onWindowClosed = (win: BrowserWindow): void => {
    if (loadingWindow === win) {
      loadingWindow = null
    }

    const layout = surfaceWindow

    if (layout?.win !== win) {
      return
    }

    clearLayoutTimers(layout)
    surfaceWindow = null

    if (surfaceOpen) {
      clearSurfaceDisplaySync()
      surfaceOpen = false
      restoreSpriteWindow()
      publish()
    }
  }

  const onWindowLoading = (win: BrowserWindow): void => {
    if (
      (surfaceWindow && !surfaceWindow.win.isDestroyed() && surfaceWindow.win !== win) ||
      (loadingWindow && !loadingWindow.isDestroyed() && loadingWindow !== win)
    ) {
      throw new Error('主窗口已存在。')
    }

    loadingWindow = win
  }

  function restoreSpriteWindow(): void {
    const sprite = options.getSpriteWindow()

    if (!options.isQuitting() && sprite && !sprite.isDestroyed()) {
      sprite.showInactive()
    }
  }

  // 隐藏已打开的入口窗并清除打开状态，不发布快照。
  const hideOpenSurface = (): void => {
    const win = surfaceWindow?.win

    if (win && !win.isDestroyed()) {
      win.hide()
    }

    clearSurfaceDisplaySync()
    surfaceOpen = false
  }

  const internalClose = (): void => {
    if (surfaceOpen) {
      hideOpenSurface()
      restoreSpriteWindow()
      publish()
    }
  }

  const internalOpen = async (
    payload: DesktopSurfaceOpenPayload,
    forcePublish = false,
    restore = false
  ): Promise<void> => {
    if (!restore && options.routeToDesktop?.(payload)) {
      publish(forcePublish)

      return
    }

    let surface = surfaceWindow

    if (!surface || surface.win.isDestroyed()) {
      surface = registerSurfaceWindow(await options.createWindow(payload))
    }

    const { win } = surface

    try {
      await options.prepareWindow?.(win, payload)
    } catch (error) {
      if (restore) {
        throw error
      }

      options.rememberLog?.(`[main-window] view restore failed: ${errorMessage(error)}`)
    }

    if (payload.page || payload.section || payload.sessionId || payload.messageKey) {
      await options.navigateWindow?.(win, payload)
    }

    // navigate/create 期间用户退出：窗口已销毁，不能再 show/focus。
    if (win.isDestroyed()) {
      onWindowClosed(win)
      publish(forcePublish)

      return
    }

    if (win.isMinimized()) {
      win.restore()
    }

    surface.minimizing = false

    surfaceOpen = true
    const sprite = options.getSpriteWindow()

    if (sprite && !sprite.isDestroyed()) {
      sprite.hide()
    }

    win.show()
    win.focus()
    bindSurfaceDisplaySync(win)

    syncSpriteToSurfaceDisplay(win)

    publish(forcePublish)
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
    return withMutex(async () => {
      if (options.routeToDesktop?.(payload)) {
        return
      }

      if (surfaceOpen && isWindowShown(surfaceWindow?.win)) {
        internalClose()

        return
      }

      await internalOpen(payload)
    })
  }

  const closeSurface = (): Promise<void> => withMutex(internalClose)

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

  const isSurfaceSender = (sender: Pick<WebContents, 'id'>): boolean => {
    return isSenderWindow(sender, surfaceWindow?.win) || isSenderWindow(sender, loadingWindow)
  }

  const refreshCompanionGeometry = (): void => {
    const layout = surfaceWindow

    if (!layout || layout.win.isDestroyed()) {
      return
    }

    const area = screen.getDisplayMatching(layout.panel).workArea

    layout.panel = {
      ...layout.panel,
      x: clamp(layout.panel.x, area.x, area.x + Math.max(0, area.width - layout.panel.width)),
      y: clamp(layout.panel.y, area.y, area.y + Math.max(0, area.height - layout.panel.height))
    }

    if (layout.preservedPanel) {
      layout.preservedPanel = { ...layout.panel }
    }

    applyCompanionLayout(layout)
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
      return openSurface(normalizeAppNavigation(payload))
    })

    const resolveWindow = (event: IpcMainInvokeEvent): BrowserWindow | null => {
      const fromSender = BrowserWindow.fromWebContents(event.sender)

      if (fromSender && !fromSender.isDestroyed()) {
        return fromSender
      }

      if (surfaceOpen) {
        const current = surfaceWindow?.win

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

        if ((!surface && !options.isPreferencesSender?.(event.sender)) || !preference) {
          throw new Error('Invalid companion preference or surface sender')
        }

        await options.saveCompanionPreference(preference)

        if (surface) {
          applyCompanionLayout(surface)
        } else if (surfaceWindow) {
          applyCompanionLayout(surfaceWindow)
        }

        publish()

        return snapshot()
      })
    )
  }

  options.rememberLog?.('[surfaces] manager ready')

  return {
    closeSurface,
    getWindow: () => {
      if (surfaceWindow && !surfaceWindow.win.isDestroyed()) {
        return surfaceWindow.win
      }

      return loadingWindow && !loadingWindow.isDestroyed() ? loadingWindow : null
    },
    isOpen: () => surfaceOpen,
    isSurfaceSender,
    minimizeWindow,
    onWindowClosed,
    onWindowLoading,
    openSurface,
    restoreMainWindow: payload => withMutex(() => internalOpen(payload, true, true)),
    publishSpriteVisibility: publish,
    registerIpcHandlers,
    toggleMaximizeWindow,
    toggleSurface,
    watchSystemEvents
  }
}
