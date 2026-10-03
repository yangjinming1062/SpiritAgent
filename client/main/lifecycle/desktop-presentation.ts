import { existsSync, unlinkSync } from 'node:fs'
import path from 'node:path'

import {
  type DesktopBackground,
  type DesktopNavigation,
  IPC,
  type PresentationMode,
  type PresentationState,
  type StageActivity,
  type StageRitualRequest,
  type SurfacePlaybackClaim
} from '@ipc/contracts'
import { app, BrowserWindow, type IpcMain, powerMonitor, screen, type WebContents } from 'electron'

import { isSenderWindow } from '../security/ipc-trust'
import { broadcastToAllWindows, createSerialQueue, errorMessage, sendToWindow } from '../shared/utils'

import { createExplorerDesktopHost } from './explorer-desktop-host'
import { createPresentationPreferences } from './presentation-preferences'

interface DesktopPresentationOptions {
  userData: string
  preloadPath: string
  backgroundPreloadPath: string
  helperPath: string
  rendererUrlFor: (role: 'desktop' | 'desktop-background', theme?: string) => string
  seedTheme: () => string | undefined
  getSpriteWindow: () => BrowserWindow | null
  isSettingsSender: (sender: WebContents) => boolean
  closeSurfaces: () => Promise<void>
  restoreSprite: () => void
  authenticated: () => boolean
  authIdentity: () => string | null
  installWindowHandlers: (win: BrowserWindow) => void
  log: (message: string) => void
  onModeChanged?: () => void
}

export function createDesktopPresentation(options: DesktopPresentationOptions) {
  const preferences = createPresentationPreferences(options.userData)
  const serial = createSerialQueue()
  let interactive: BrowserWindow | null = null
  const backgrounds = new Set<BrowserWindow>()
  const displayBoundsByWindow = new WeakMap<BrowserWindow, Electron.Rectangle>()
  let revision = 0
  let stageEpoch = 0
  let stageVisible = true
  let foreground = false
  let effectiveMode: PresentationMode = 'window'
  let status: PresentationState['status'] = 'inactive'
  let notifiedMode: PresentationMode | undefined
  let notifiedStatus: PresentationState['status'] | undefined
  let failureReason: string | null = null
  let hostReady = false
  let suppressAutoRestore = false
  let lastHeartbeat = 0
  let startupGeneration = 0
  const readyWindows = new Map<BrowserWindow, { resolve: () => void; reject: (error: Error) => void }>()
  let initialized = false
  let displayTimer: ReturnType<typeof setTimeout> | undefined
  let healthTimer: ReturnType<typeof setInterval> | undefined
  let lastActivity: StageActivity = { locked: false, idleSeconds: -1, effectiveTier: 'normal', focus: null }
  let currentBackground: DesktopBackground = { image: null, theme: 'day-clear', reduceMotion: false }
  const claims = new Map<string, number>()

  const rituals = new Map<
    string,
    { epoch: number; resolve: (completed: boolean) => void; timer: ReturnType<typeof setTimeout> }
  >()

  const native = createExplorerDesktopHost({
    helperPath: options.helperPath,
    journalPath: path.join(options.userData, 'desktop-shell-recovery.json'),
    log: options.log,
    onForegroundChanged: active => {
      if (foreground === active) {
        return
      }

      foreground = active

      if (!active) {
        clearRituals()
      }

      publish()
    },
    onFailure: reason => {
      if (status === 'inactive' || status === 'failed') {
        return
      }

      scheduleRecovery(reason)
    }
  })

  function snapshot(): PresentationState {
    const displays = app.isReady()
      ? screen.getAllDisplays().map(display => ({
          id: display.id,
          label: display.label || `显示器 ${display.id}`,
          width: display.bounds.width,
          height: display.bounds.height
        }))
      : []

    const preferred = preferences.get()

    const actual =
      interactive && !interactive.isDestroyed() ? screen.getDisplayMatching(interactive.getBounds()).id : null

    return {
      requestedMode: preferred.mode,
      effectiveMode,
      status,
      failureReason,
      supported: process.platform === 'win32',
      foreground: foreground && effectiveMode === 'desktop' && powerMonitor.getSystemIdleState(1) !== 'locked',
      displayId: actual ?? preferred.displayId,
      displays,
      stageOwner: effectiveMode === 'desktop' ? 'desktop' : 'sprite',
      stageVisible,
      stageEpoch,
      revision
    }
  }

  function publish(): void {
    revision += 1
    broadcastToAllWindows(IPC.event.presentationChanged, snapshot())

    if (notifiedMode !== effectiveMode || notifiedStatus !== status) {
      notifiedMode = effectiveMode
      notifiedStatus = status
      options.onModeChanged?.()
    }
  }

  function cancelRitual(callId: string): void {
    const ritual = rituals.get(callId)

    if (!ritual) {
      return
    }

    clearTimeout(ritual.timer)
    rituals.delete(callId)

    sendToWindow(interactive, IPC.event.presentationRitualCancelled, { callId, epoch: ritual.epoch })
    ritual.resolve(false)
  }

  function clearRituals(): void {
    for (const callId of rituals.keys()) {
      cancelRitual(callId)
    }
  }

  function destroyWindows(): void {
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
    currentBackground = { image: null, theme: options.seedTheme() ?? 'day-clear', reduceMotion: false }
  }

  function scheduleRecovery(reason: string): void {
    if (status === 'inactive' || status === 'failed') {
      return
    }

    invalidateStartup(reason)
    foreground = false
    clearRituals()
    publish()

    void serial(async () => {
      if (status === 'inactive' || status === 'failed') {
        return
      }

      await leave(reason)
    }).catch(error => options.log(`[desktop] recovery: ${errorMessage(error)}`))
  }

  function invalidateStartup(reason: string): void {
    startupGeneration += 1

    for (const waiting of readyWindows.values()) {
      waiting.reject(new Error(reason))
    }
  }

  async function leave(reason?: string): Promise<void> {
    let finalReason = reason
    clearTimeout(displayTimer)
    clearInterval(healthTimer)
    healthTimer = undefined
    status = 'recovering'
    clearRituals()
    stageEpoch += 1
    claims.clear()
    publish()

    try {
      await native.stop()
    } catch (error) {
      options.log(`[desktop] restore failed: ${errorMessage(error)}`)

      try {
        await native.recover()
      } catch (recoveryError) {
        finalReason = `系统桌面恢复失败：${errorMessage(recoveryError)}`
        throw recoveryError
      }
    } finally {
      destroyWindows()
      foreground = false
      effectiveMode = 'window'
      status = finalReason ? 'failed' : 'inactive'
      failureReason = finalReason ?? null

      if (finalReason) {
        suppressAutoRestore = true
      }

      publish()
      options.restoreSprite()
    }
  }

  function makeWindow(display: Electron.Display, primary: boolean): BrowserWindow {
    const win = new BrowserWindow({
      ...display.bounds,
      frame: false,
      thickFrame: false,
      show: false,
      skipTaskbar: true,
      resizable: false,
      fullscreenable: false,
      hasShadow: false,
      backgroundColor: '#111827',
      title: primary ? '唤生桌面' : '唤生背景',
      webPreferences: {
        preload: primary ? options.preloadPath : options.backgroundPreloadPath,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        devTools: !app.isPackaged
      }
    })

    win.setBounds(display.bounds)
    displayBoundsByWindow.set(win, { ...display.bounds })

    win.webContents.on('render-process-gone', (_event, details) => {
      if (status === 'starting' || status === 'active') {
        scheduleRecovery(`桌面渲染进程已退出：${details.reason}`)
      }
    })
    win.on('unresponsive', () => {
      if (status === 'starting' || status === 'active') {
        scheduleRecovery('桌面暂时无响应，已恢复系统桌面。')
      }
    })

    if (primary) {
      options.installWindowHandlers(win)
      const expected = new URL(options.rendererUrlFor('desktop'))

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

    win.on('closed', () => {
      if (status === 'starting' || status === 'active') {
        scheduleRecovery('桌面窗口已关闭，已恢复系统桌面。')
      }
    })

    return win
  }

  async function loadWindow(win: BrowserWindow, primary: boolean): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined

    const loading = new Promise<void>((resolve, reject) => {
      const ready = new Promise<void>(reportReady => {
        readyWindows.set(win, { resolve: reportReady, reject })
      })

      timer = setTimeout(() => reject(new Error('桌面界面准备超时。')), 15000)
      void Promise.all([
        ready,
        win.loadURL(options.rendererUrlFor(primary ? 'desktop' : 'desktop-background', options.seedTheme()))
      ]).then(() => resolve(), reject)
    })

    try {
      await loading
    } finally {
      clearTimeout(timer)
      readyWindows.delete(win)
    }
  }

  async function enter(): Promise<void> {
    if (effectiveMode === 'desktop') {
      return
    }

    if (process.platform !== 'win32') {
      throw new Error('桌面模式目前仅支持 Windows。')
    }

    if (!options.authenticated() || !hostReady) {
      throw new Error('请先完成账户激活与伙伴准备。')
    }

    if (!existsSync(options.helperPath)) {
      throw new Error(
        app.isPackaged
          ? 'Windows 桌面组件缺失，请重新安装客户端。'
          : 'Windows 桌面组件尚未准备，请在 client 目录运行 pnpm build:native；需要 Rust、Visual Studio C++ 构建工具和 Windows SDK。'
      )
    }

    const startingIdentity = options.authIdentity()
    const currentGeneration = startupGeneration

    const assertAttempt = (): void => {
      if (
        currentGeneration !== startupGeneration ||
        !options.authenticated() ||
        options.authIdentity() !== startingIdentity ||
        !hostReady
      ) {
        throw new Error('桌面准备期间账户或窗口已失效。')
      }
    }

    status = 'starting'
    failureReason = null
    stageEpoch += 1
    publish()

    try {
      const allDisplays = screen.getAllDisplays()

      const selected =
        allDisplays.find(display => display.id === preferences.get().displayId) ?? screen.getPrimaryDisplay()

      await options.closeSurfaces()
      assertAttempt()
      interactive = makeWindow(selected, true)
      const main = interactive
      await loadWindow(main, true)
      assertAttempt()

      for (const display of allDisplays) {
        if (display.id === selected.id) {
          continue
        }

        const win = makeWindow(display, false)
        backgrounds.add(win)
      }

      await Promise.all([...backgrounds].map(win => loadWindow(win, false)))

      const windows = [main, ...backgrounds]

      const assertReady = (): void => {
        assertAttempt()

        if (windows.some(win => win.isDestroyed())) {
          throw new Error('桌面准备期间账户或窗口已失效。')
        }
      }

      assertReady()

      await native.start({
        parentPid: process.pid,
        takeover: process.env.SPIRITAGENT_DESKTOP_PROBE !== '1',
        windows: windows.map(win => {
          const bounds = displayBoundsByWindow.get(win)

          if (!bounds) {
            throw new Error('桌面窗口缺少显示器边界。')
          }

          return { handle: win.getNativeWindowHandle(), bounds: screen.dipToScreenRect(win, bounds) }
        })
      })

      assertReady()

      for (const win of windows) {
        win.showInactive()
      }

      assertReady()

      effectiveMode = 'desktop'
      status = 'active'
      lastHeartbeat = Date.now()
      options.getSpriteWindow()?.hide()
      publish()
      healthTimer = setInterval(() => {
        if (Date.now() - lastHeartbeat > 10000) {
          clearInterval(healthTimer)
          scheduleRecovery('桌面连接中断，已恢复系统桌面。')
        }
      }, 2000)
    } catch (error) {
      await leave(errorMessage(error))
      throw error
    }
  }

  async function initialize(): Promise<void> {
    if (initialized) {
      return
    }

    initialized = true
    const interruptedFile = path.join(options.userData, 'desktop-shell-recovery.json.interrupted')
    let interrupted = existsSync(interruptedFile)
    suppressAutoRestore = interrupted

    if (process.platform === 'win32' && existsSync(path.join(options.userData, 'desktop-shell-recovery.json'))) {
      try {
        suppressAutoRestore = (await native.recover()) || interrupted
      } catch (error) {
        suppressAutoRestore = true
        failureReason = `系统桌面恢复失败：${errorMessage(error)}`
        status = 'failed'
        options.log(`[desktop] startup recovery: ${errorMessage(error)}`)
      }

      if (suppressAutoRestore && !failureReason) {
        failureReason = '上次桌面异常退出，已恢复系统桌面。请重新选择桌面模式。'
        status = 'failed'
      }
    }

    interrupted ||= existsSync(interruptedFile)
    suppressAutoRestore ||= interrupted

    if (interrupted && !failureReason) {
      failureReason = '上次桌面异常中断，已保留窗口模式。请重新选择桌面模式。'
      status = 'failed'
    }

    if (interrupted && !existsSync(path.join(options.userData, 'desktop-shell-recovery.json'))) {
      try {
        unlinkSync(interruptedFile)
      } catch (error) {
        options.log(`[desktop] interruption marker cleanup: ${errorMessage(error)}`)
      }
    }

    const onDisplays = (): void => {
      if (status === 'starting') {
        scheduleRecovery('桌面准备期间显示器已变化，请重新选择桌面模式。')
      } else if (status === 'active') {
        const currentGeneration = startupGeneration
        clearTimeout(displayTimer)
        displayTimer = setTimeout(() => {
          void serial(async () => {
            if (status !== 'active' || currentGeneration !== startupGeneration) {
              return
            }

            await leave()

            if (currentGeneration !== startupGeneration) {
              return
            }

            await enter()
          }).catch(error => options.log(errorMessage(error)))
        }, 100)
      } else {
        publish()
      }
    }

    screen.on('display-added', onDisplays)
    screen.on('display-removed', onDisplays)
    screen.on('display-metrics-changed', (_event, _display, metrics) => {
      if (metrics.some(metric => ['bounds', 'scaleFactor', 'rotation'].includes(metric))) {
        onDisplays()
      }
    })
    powerMonitor.on('lock-screen', () => {
      foreground = false
      clearRituals()
      publish()
    })
    powerMonitor.on('unlock-screen', publish)
    powerMonitor.on('suspend', () => {
      scheduleRecovery('电脑已进入休眠，已恢复窗口模式。')
    })
    powerMonitor.on('resume', () => {
      scheduleRecovery('电脑已从休眠恢复，已恢复窗口模式。')
    })
    publish()
  }

  const isDesktopSender = (sender: Pick<WebContents, 'id'>): boolean => isSenderWindow(sender, interactive)

  function assertDesktop(sender: WebContents): void {
    if (!isDesktopSender(sender)) {
      throw new Error('仅桌面入口允许此操作。')
    }
  }

  function assertSettings(sender: WebContents): void {
    if (!isDesktopSender(sender) && !options.isSettingsSender(sender)) {
      throw new Error('仅本机设置入口允许此操作。')
    }
  }

  function setMode(mode: PresentationMode, sender?: WebContents): Promise<PresentationState> {
    if (sender) {
      assertSettings(sender)
    }

    if (mode === 'desktop' && process.platform !== 'win32') {
      return Promise.reject(new Error('桌面模式目前仅支持 Windows。'))
    }

    if (mode === 'window') {
      invalidateStartup('桌面准备已取消。')
    }

    const currentGeneration = startupGeneration

    return serial(async () => {
      if (sender) {
        assertSettings(sender)
      }

      await preferences.set({ mode })

      if (mode === 'desktop' && currentGeneration !== startupGeneration) {
        return snapshot()
      }

      suppressAutoRestore = false

      if (mode === 'desktop') {
        try {
          await enter()
        } catch (error) {
          if (status !== 'failed') {
            status = 'failed'
            failureReason = errorMessage(error)
            publish()
          }

          throw error
        }
      } else {
        await leave()
      }

      return snapshot()
    })
  }

  function registerIpc(ipcMain: IpcMain): void {
    ipcMain.handle(IPC.invoke.presentationRitualCancel, (event, callId: unknown) => {
      if (!isSenderWindow(event.sender, options.getSpriteWindow())) {
        throw new Error('仅精灵宿主允许取消仪式。')
      }

      if (typeof callId !== 'string') {
        throw new Error('无效调用。')
      }

      cancelRitual(callId)
    })
    ipcMain.handle(IPC.invoke.presentationGetStageActivity, event => {
      assertDesktop(event.sender)

      return lastActivity
    })
    ipcMain.handle(IPC.invoke.presentationSetStageVisible, (event, visible: unknown) => {
      assertDesktop(event.sender)

      if (typeof visible !== 'boolean') {
        throw new Error('无效舞台状态。')
      }

      stageVisible = visible

      if (!visible) {
        clearRituals()
      }

      publish()
    })
    ipcMain.handle(IPC.invoke.presentationGetState, () => snapshot())
    ipcMain.handle(IPC.invoke.presentationSetMode, (event, raw: unknown) => {
      assertSettings(event.sender)

      if (raw !== 'desktop' && raw !== 'window') {
        throw new Error('无效呈现方式。')
      }

      return setMode(raw, event.sender)
    })
    ipcMain.handle(IPC.invoke.presentationSetDisplay, (event, id: unknown) => {
      assertSettings(event.sender)

      if (typeof id !== 'number' || !screen.getAllDisplays().some(display => display.id === id)) {
        throw new Error('显示器不可用。')
      }

      const currentGeneration = startupGeneration

      return serial(async () => {
        assertSettings(event.sender)
        await preferences.set({ displayId: id })

        if (effectiveMode === 'desktop' && currentGeneration === startupGeneration) {
          await leave()

          if (currentGeneration === startupGeneration) {
            await enter()
          }
        }

        publish()

        return snapshot()
      })
    })
    ipcMain.handle(IPC.invoke.presentationReportReady, event => {
      assertDesktop(event.sender)

      if (interactive) {
        readyWindows.get(interactive)?.resolve()
      }

      lastHeartbeat = Date.now()
    })
    ipcMain.handle(IPC.invoke.presentationFocus, (event, epoch: unknown) => {
      assertDesktop(event.sender)

      if (typeof epoch !== 'number' || !Number.isSafeInteger(epoch)) {
        throw new Error('无效桌面代次。')
      }

      const currentGeneration = startupGeneration
      const identity = options.authIdentity()

      return serial(async () => {
        assertDesktop(event.sender)

        const eligible = (): boolean =>
          status === 'active' &&
          effectiveMode === 'desktop' &&
          epoch === stageEpoch &&
          currentGeneration === startupGeneration &&
          !lastActivity.locked &&
          powerMonitor.getSystemIdleState(1) !== 'locked' &&
          options.authenticated() &&
          identity === options.authIdentity() &&
          isSenderWindow(event.sender, interactive)

        if (!eligible()) {
          return false
        }

        return native.focus(interactive!.getNativeWindowHandle(), eligible)
      })
    })
    ipcMain.handle(IPC.invoke.presentationHeartbeat, event => {
      assertDesktop(event.sender)
      lastHeartbeat = Date.now()
    })
    ipcMain.handle(IPC.invoke.presentationHostReady, event => {
      if (!isSenderWindow(event.sender, options.getSpriteWindow())) {
        throw new Error('仅精灵宿主允许恢复桌面。')
      }

      hostReady = true

      if (preferences.get().mode !== 'desktop' || suppressAutoRestore || effectiveMode === 'desktop') {
        return
      }

      const currentGeneration = startupGeneration

      return serial(async () => {
        await initialize()

        if (currentGeneration === startupGeneration && !suppressAutoRestore && preferences.get().mode === 'desktop') {
          await enter()
        }
      }).catch(error => options.log(`[desktop] auto restore: ${errorMessage(error)}`))
    })
    ipcMain.handle(IPC.invoke.presentationSetBackground, (event, raw: unknown) => {
      assertDesktop(event.sender)

      if (!raw || typeof raw !== 'object') {
        throw new Error('无效背景。')
      }

      const value = raw as Partial<DesktopBackground>

      if (
        (value.image !== null &&
          (typeof value.image !== 'string' ||
            value.image.length > 48_000_000 ||
            !/^data:image\/(?:png|jpeg|webp|gif);base64,/.test(value.image))) ||
        typeof value.theme !== 'string' ||
        typeof value.reduceMotion !== 'boolean'
      ) {
        throw new Error('无效背景。')
      }

      currentBackground = { image: value.image, theme: value.theme, reduceMotion: value.reduceMotion }

      for (const win of backgrounds) {
        sendToWindow(win, IPC.event.backgroundImage, currentBackground)
      }
    })
    ipcMain.handle(IPC.invoke.backgroundReady, event => {
      const win = [...backgrounds].find(candidate => isSenderWindow(event.sender, candidate))

      if (!win) {
        throw new Error('无效背景窗口。')
      }

      sendToWindow(win, IPC.event.backgroundImage, currentBackground)
      readyWindows.get(win)?.resolve()
    })
    ipcMain.handle(IPC.invoke.presentationClaimPlay, (event, raw: unknown) => {
      assertDesktop(event.sender)
      const claim = raw as Partial<SurfacePlaybackClaim> | null

      if (
        !snapshot().foreground ||
        !stageVisible ||
        status !== 'active' ||
        typeof claim?.playId !== 'string' ||
        !/^[a-f0-9]{32}$/i.test(claim.playId)
      ) {
        return false
      }

      const expires =
        claim.expiresAt === null ? Infinity : typeof claim.expiresAt === 'string' ? Date.parse(claim.expiresAt) : NaN

      if (Number.isNaN(expires) || expires < Date.now()) {
        return false
      }

      for (const [key, deadline] of claims) {
        if (deadline < Date.now()) {
          claims.delete(key)
        }
      }

      if (claims.has(claim.playId)) {
        return false
      }

      claims.set(claim.playId, expires)

      return true
    })
    ipcMain.handle(IPC.invoke.presentationStageActivity, (event, activity: StageActivity) => {
      if (!isSenderWindow(event.sender, options.getSpriteWindow())) {
        throw new Error('仅精灵宿主允许上报活动。')
      }

      if (
        !activity ||
        typeof activity.locked !== 'boolean' ||
        typeof activity.idleSeconds !== 'number' ||
        !Number.isFinite(activity.idleSeconds) ||
        !['autonomous', 'normal', 'still'].includes(activity.effectiveTier)
      ) {
        throw new Error('无效活动。')
      }

      lastActivity = activity

      sendToWindow(interactive, IPC.event.presentationStageActivity, activity)
    })
    ipcMain.handle(IPC.invoke.presentationRitualRequest, (event, raw: Omit<StageRitualRequest, 'epoch'>) => {
      if (!isSenderWindow(event.sender, options.getSpriteWindow())) {
        throw new Error('仅精灵宿主允许请求仪式。')
      }

      if (
        !snapshot().foreground ||
        !stageVisible ||
        status !== 'active' ||
        !interactive ||
        !raw ||
        typeof raw.callId !== 'string' ||
        !raw.rect ||
        ![raw.rect.x, raw.rect.y, raw.rect.w, raw.rect.h].every(Number.isFinite)
      ) {
        return false
      }

      return new Promise<boolean>(resolve => {
        if (rituals.has(raw.callId)) {
          resolve(false)

          return
        }

        const timer = setTimeout(() => cancelRitual(raw.callId), 10000)

        rituals.set(raw.callId, { epoch: stageEpoch, resolve, timer })
        sendToWindow(interactive, IPC.event.presentationRitual, { ...raw, epoch: stageEpoch })
      })
    })
    ipcMain.handle(
      IPC.invoke.presentationRitualComplete,
      (event, reply: { callId: string; epoch: number; completed: boolean }) => {
        assertDesktop(event.sender)
        const pending = reply && rituals.get(reply.callId)

        if (!pending || reply.epoch !== pending.epoch || reply.epoch !== stageEpoch) {
          return
        }

        clearTimeout(pending.timer)
        rituals.delete(reply.callId)
        pending.resolve(reply.completed === true && snapshot().foreground && stageVisible && status === 'active')
      }
    )
  }

  return {
    getState: snapshot,
    setMode,
    getWindow: () => interactive,
    isDesktopSender,
    registerIpc,
    initialize,
    stop: () => {
      invalidateStartup('桌面准备已取消。')

      return serial(() => leave())
    },
    accountChanged: () => {
      hostReady = false
      invalidateStartup('桌面准备期间账户已变化。')

      return serial(async () => {
        if (status !== 'inactive') {
          await leave()
        }
      })
    },
    navigate: (payload: DesktopNavigation): boolean => {
      if (effectiveMode !== 'desktop' || !interactive || interactive.isDestroyed()) {
        return false
      }

      sendToWindow(interactive, IPC.event.desktopNavigate, payload)

      return true
    }
  }
}

export type DesktopPresentation = ReturnType<typeof createDesktopPresentation>
