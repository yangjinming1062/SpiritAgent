// 主窗口生命周期与内嵌伙伴的实际可见资格。

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

import { PANEL_SIZE, parseCompanionPreference } from './surface-companion'

export interface SurfacesManager {
  closeSurface: () => Promise<void>
  getWindow: () => BrowserWindow | null
  isOpen: () => boolean
  /** sender 是否为该入口面当前窗口的 webContents；用于只允许特定入口调用的通道。 */
  isSurfaceSender: (sender: Pick<WebContents, 'id'>) => boolean
  minimizeWindow: (win: BrowserWindow) => void
  onWindowClosed: (win: BrowserWindow) => void
  onWindowLoading: (win: BrowserWindow) => void
  openSurface: (payload?: DesktopSurfaceOpenPayload) => Promise<void>
  restoreMainWindow: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  /** 精灵窗显示、隐藏、最小化或还原后调用，向各窗口发布含 `spriteVisible` 的新快照。 */
  publishSpriteVisibility: () => void
  registerIpcHandlers: (deps: { ipcMain: IpcMain }) => void
  toggleMaximizeWindow: (win: BrowserWindow) => void
  toggleSurface: (payload?: DesktopSurfaceOpenPayload) => Promise<void>
  /** 跟随锁屏与显示器变化，须在 app ready 后调用一次。 */
  watchSystemEvents: () => void
}

interface SurfacesManagerOptions {
  authIdentity: () => string | null
  createWindow: (payload?: DesktopSurfaceOpenPayload) => Promise<BrowserWindow>
  getCompanionPreference: () => SurfaceCompanionPreference
  getSpriteWindow: () => BrowserWindow | null
  isQuitting: () => boolean
  isPreferencesSender: (sender: WebContents) => boolean
  routeToDesktop: (payload: DesktopSurfaceOpenPayload) => boolean
  prepareWindow: (win: BrowserWindow, payload: DesktopSurfaceOpenPayload) => Promise<void>
  navigateWindow: (win: BrowserWindow, payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  saveCompanionPreference: (preference: SurfaceCompanionPreference) => Promise<void>
  rememberLog: (chunk: string) => void
  syncSpriteToDisplay: (display: Electron.Display) => void
}

interface CallLayout {
  bounds: Rectangle
  expanded: boolean
  maximized: boolean
  normalBounds: Rectangle | null
}

interface SurfaceWindowState {
  win: BrowserWindow
  minimizing: boolean
  normalBounds: Rectangle | null
  expanded: boolean
  call: CallLayout | null
}

// 通话占满整窗时临时压低最小尺寸；退出后恢复创建时的下限。
const CALL_WINDOW_SIZE = { width: 420, height: 760 }
const CALL_WINDOW_MIN = { width: 320, height: 480 }

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  let surfaceWindow: SurfaceWindowState | null = null
  let loadingWindow: BrowserWindow | null = null
  let surfaceOpen = false
  const withMutex = createSerialQueue()
  let unbindSurfaceDisplaySync: null | (() => void) = null
  let screenLocked = false
  let stateRevision = 0
  let lastPublishedState = ''
  let companionVisibility: { senderId: number; authSessionId: string; visible: boolean } | null = null

  function findSurfaceWindow(win: BrowserWindow | null): SurfaceWindowState | undefined {
    return surfaceWindow && surfaceWindow.win === win ? surfaceWindow : undefined
  }

  function isCurrentWindow(surface: SurfaceWindowState): boolean {
    return surfaceWindow === surface && !surface.win.isDestroyed()
  }

  function applyWindowBounds(win: BrowserWindow, bounds: Rectangle): void {
    if (process.platform === 'win32') {
      win.setContentBounds(bounds)
    } else {
      win.setBounds(bounds)
    }
  }

  // 竖版通话窗固定居中于窗口所在显示器，显示器变化时按当前工作区重新计算。
  function callWindowBounds(win: BrowserWindow): Rectangle {
    const current = process.platform === 'win32' ? win.getContentBounds() : win.getBounds()
    const area = screen.getDisplayMatching(current).workArea
    const width = Math.min(CALL_WINDOW_SIZE.width, Math.max(1, area.width - 32))
    const height = Math.min(CALL_WINDOW_SIZE.height, Math.max(1, area.height - 32))

    return {
      width,
      height,
      x: Math.round(area.x + (area.width - width) / 2),
      y: Math.round(area.y + (area.height - height) / 2)
    }
  }

  function setCallLayout(win: BrowserWindow, active: boolean): void {
    const layout = findSurfaceWindow(win)

    if (!layout || win.isDestroyed()) {
      return
    }

    if (active) {
      // 隐藏或最小化中的窗口不接受迟到的接管请求，避免下次打开时停留在竖版。
      if (layout.call || !win.isVisible()) {
        return
      }

      const target = callWindowBounds(win)

      layout.call = {
        bounds: process.platform === 'win32' ? win.getContentBounds() : win.getBounds(),
        expanded: layout.expanded,
        maximized: win.isMaximized(),
        normalBounds: layout.normalBounds
      }
      win.setMinimumSize(Math.min(CALL_WINDOW_MIN.width, target.width), Math.min(CALL_WINDOW_MIN.height, target.height))
      applyWindowBounds(win, target)

      return
    }

    const call = layout.call

    if (!call) {
      return
    }

    win.setMinimumSize(PANEL_SIZE.minWidth, PANEL_SIZE.minHeight)

    if (!call.maximized && win.isMaximized()) {
      win.unmaximize()
    }

    if (call.maximized) {
      win.maximize()
    } else if (call.expanded) {
      win.setContentBounds(screen.getDisplayMatching(call.bounds).workArea)
    } else {
      applyWindowBounds(win, fitWindowBounds(call.bounds))
    }

    // 先完成尺寸恢复再写回还原信息，避免恢复自身触发的 will-resize 清掉它们。
    layout.normalBounds = call.normalBounds
    layout.expanded = call.expanded
    layout.call = null
  }

  function companionState(): SurfaceCompanionState {
    const preference = options.getCompanionPreference()
    const layout = surfaceWindow
    const win = layout?.win
    const isOpen = surfaceOpen && !!win && !win.isDestroyed()
    const isActive = isOpen && win.isVisible()
    const minimized = isOpen && (win.isMinimized() || !!layout?.minimizing)

    const rendered =
      companionVisibility?.senderId === win?.webContents.id &&
      companionVisibility?.authSessionId === options.authIdentity() &&
      companionVisibility?.visible === true

    const hiddenReason = minimized
      ? 'minimized'
      : !isActive
        ? 'window-hidden'
        : screenLocked
          ? 'screen-locked'
          : !rendered
            ? 'layout'
            : null

    return { hiddenReason, preference, visible: preference.enabled && hiddenReason === null }
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

  function registerSurfaceWindow(win: BrowserWindow): SurfaceWindowState {
    const layout: SurfaceWindowState = { win, minimizing: false, normalBounds: null, expanded: false, call: null }
    surfaceWindow = layout

    if (loadingWindow === win) {
      loadingWindow = null
    }

    win.on('show', () => publish())
    win.on('hide', () => {
      setCallLayout(win, false)
      publish()
    })
    win.on('maximize', () => publish())
    win.on('unmaximize', () => publish())
    win.on('minimize', () => {
      setCallLayout(win, false)
      publish()
    })
    // 渲染进程重载或崩溃时没有退出回报，窗口不能停留在竖版尺寸。
    win.webContents.on('render-process-gone', () => setCallLayout(win, false))
    win.webContents.on('did-start-loading', () => setCallLayout(win, false))

    const releaseExpandedLayout = (): void => {
      // 通话布局的进入/恢复本身就是一次 resize，不能让它清掉待恢复的最大化信息。
      if (layout.call) {
        return
      }

      if (layout.normalBounds || layout.expanded) {
        layout.normalBounds = null
        layout.expanded = false
        publish(true)
      }
    }

    win.on('will-move', releaseExpandedLayout)
    win.on('will-resize', releaseExpandedLayout)
    win.on('restore', () => {
      if (isCurrentWindow(layout)) {
        layout.minimizing = false
        publish()
      }
    })
    publish()

    return layout
  }

  // 完整入口开启时桌面精灵窗虽隐藏，仍须跟随到同屏，确保关闭后原地恢复；这是主进程窗口副作用，不属于渲染层表面状态。
  function syncSpriteToSurfaceDisplay(win: BrowserWindow): void {
    if (win.isDestroyed()) {
      return
    }

    options.syncSpriteToDisplay(screen.getDisplayMatching(win.getBounds()))
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

    companionVisibility = null
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
    payload?: DesktopSurfaceOpenPayload,
    forcePublish = false,
    restore = false
  ): Promise<void> => {
    if (!restore && payload && options.routeToDesktop(payload)) {
      publish(forcePublish)

      return
    }

    let surface = surfaceWindow

    if (!surface || surface.win.isDestroyed()) {
      surface = registerSurfaceWindow(await options.createWindow(payload))
    }

    const { win } = surface

    try {
      if (payload) {
        await options.prepareWindow(win, payload)
      }
    } catch (error) {
      if (restore) {
        throw error
      }

      options.rememberLog(`[main-window] view restore failed: ${errorMessage(error)}`)
    }

    if (payload) {
      const focused = payload.focus.page === 'chat' && Boolean(payload.focus.sessionId || payload.focus.messageKey)

      if (payload.location.page !== 'chat' || focused) {
        await options.navigateWindow(win, payload)
      }
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

  const openSurface = (payload?: DesktopSurfaceOpenPayload): Promise<void> =>
    withMutex(async () => {
      try {
        // open 的调用方先写入本地意图，即使实际状态未变也要回灌权威快照。
        await internalOpen(payload, true)
      } catch (error) {
        publish(true)
        throw error
      }
    })

  const toggleSurface = (payload?: DesktopSurfaceOpenPayload): Promise<void> => {
    return withMutex(async () => {
      if (payload && options.routeToDesktop(payload)) {
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

    if (layout?.call) {
      return
    }

    // Windows 透明窗的原生展开状态不可靠，使用工作区矩形并保留还原位置。
    if (process.platform === 'win32' && layout) {
      if (layout.expanded && layout.normalBounds) {
        layout.expanded = false
        layout.normalBounds = fitWindowBounds(layout.normalBounds)
        win.setContentBounds(layout.normalBounds)
      } else {
        const current = win.getContentBounds()
        layout.normalBounds ??= current
        layout.expanded = true
        win.setContentBounds(screen.getDisplayMatching(current).workArea)
      }

      publish(true)

      return
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

  function fitWindowBounds(bounds: Rectangle): Rectangle {
    const area = screen.getDisplayMatching(bounds).workArea
    const width = Math.min(bounds.width, area.width)
    const height = Math.min(bounds.height, area.height)

    return {
      width,
      height,
      x: clamp(bounds.x, area.x, area.x + Math.max(0, area.width - width)),
      y: clamp(bounds.y, area.y, area.y + Math.max(0, area.height - height))
    }
  }

  const refreshWindowGeometry = (): void => {
    const win = surfaceWindow?.win

    if (!win || win.isDestroyed() || win.isMaximized() || win.isMinimized()) {
      return
    }

    if (surfaceWindow?.call) {
      applyWindowBounds(win, callWindowBounds(win))

      return
    }

    const bounds = process.platform === 'win32' ? win.getContentBounds() : win.getBounds()
    const target = surfaceWindow?.expanded ? screen.getDisplayMatching(bounds).workArea : fitWindowBounds(bounds)

    if (surfaceWindow?.normalBounds && !surfaceWindow.expanded) {
      surfaceWindow.normalBounds = target
    }

    if (process.platform === 'win32') {
      win.setContentBounds(target)
    } else {
      win.setBounds(target)
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
    screen.on('display-added', () => refreshWindowGeometry())
    screen.on('display-removed', () => refreshWindowGeometry())
    screen.on('display-metrics-changed', () => refreshWindowGeometry())
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
    ipcMain.handle(IPC.invoke.surfaceCallLayout, (event, payload: unknown) => {
      const win = BrowserWindow.fromWebContents(event.sender)
      const active = payload && typeof payload === 'object' ? (payload as { active?: unknown }).active : undefined

      if (!win || win.isDestroyed() || !isSurfaceSender(event.sender) || typeof active !== 'boolean') {
        throw new Error('Invalid call layout request')
      }

      return withMutex(() => setCallLayout(win, active))
    })
    ipcMain.handle(IPC.invoke.surfaceIsMaximized, event => {
      const win = resolveWindow(event)

      return Boolean((win && findSurfaceWindow(win)?.expanded) || win?.isMaximized())
    })
    ipcMain.handle(IPC.invoke.surfaceSetIgnoreMouseEvents, (event, payload?: { forward?: boolean; ignore: boolean }) =>
      setWindowIgnoreMouseEvents(resolveWindow(event), payload)
    )
    ipcMain.handle(IPC.invoke.surfaceGetState, () => snapshot())
    ipcMain.handle(IPC.invoke.surfaceCompanionVisibility, (event, payload: unknown) => {
      const identity = options.authIdentity()

      if (
        !isSurfaceSender(event.sender) ||
        !payload ||
        typeof payload !== 'object' ||
        !identity ||
        !('authSessionId' in payload) ||
        payload.authSessionId !== identity ||
        !('visible' in payload) ||
        typeof payload.visible !== 'boolean'
      ) {
        throw new Error('Invalid companion visibility or surface sender')
      }

      companionVisibility = { senderId: event.sender.id, authSessionId: identity, visible: payload.visible }
      publish()

      return snapshot()
    })
    ipcMain.handle(IPC.invoke.surfaceSetCompanion, (event, raw: unknown) =>
      withMutex(async () => {
        const surface = findSurfaceWindow(BrowserWindow.fromWebContents(event.sender))
        const preference = parseCompanionPreference(raw)

        if ((!surface && !options.isPreferencesSender(event.sender)) || !preference) {
          throw new Error('Invalid companion preference or surface sender')
        }

        await options.saveCompanionPreference(preference)

        publish()

        return snapshot()
      })
    )
  }

  options.rememberLog('[surfaces] manager ready')

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
