import { existsSync, unlinkSync } from 'node:fs'
import path from 'node:path'

import {
  DESKTOP_COMPANION_ACTIVITY_PRIORITY,
  type DesktopBackground,
  type DesktopCompanionActivityState,
  type DesktopCompanionInteraction,
  type DesktopNavigation,
  type DesktopStageInsets,
  IPC,
  type PresentationMode,
  type PresentationState,
  type StageActivity,
  type StageRitualRequest
} from '@ipc/contracts'
import { app, BrowserWindow, type IpcMain, powerMonitor, screen, type WebContents } from 'electron'

import { isSenderWindow } from '../security/ipc-trust'
import type { RunningApplicationsState } from '../shared/desktop-applications'
import {
  atomicWriteFile,
  broadcastToAllWindows,
  createPlaybackClaims,
  createSerialQueue,
  errorMessage,
  sendToWindow
} from '../shared/utils'

import { createExplorerDesktopHost } from './explorer-desktop-host'
import { createPresentationPreferences } from './presentation-preferences'

interface DesktopPresentationOptions {
  userData: string
  preloadPath: string
  backgroundPreloadPath: string
  helperPath: string
  rendererUrlFor: (role: 'desktop' | 'desktop-background' | 'desktop-companion', theme?: string) => string
  seedTheme: () => string | undefined
  getSpriteWindow: () => BrowserWindow | null
  isSettingsSender: (sender: WebContents) => boolean
  closeSurfaces: () => Promise<void>
  restoreSprite: () => void
  authenticated: () => boolean
  authIdentity: () => string | null
  installWindowHandlers: (win: BrowserWindow, options?: { reloadOnCrash?: boolean }) => void
  lockZoom: (win: BrowserWindow) => void
  log: (message: string) => void
  onModeChanged?: () => void
}

export function createDesktopPresentation(options: DesktopPresentationOptions) {
  const preferences = createPresentationPreferences(options.userData)
  const serial = createSerialQueue()
  const journalFile = path.join(options.userData, 'desktop-shell-recovery.json')
  const interruptedFile = `${journalFile}.interrupted`
  const mainInterruptedFile = `${journalFile}.main-interrupted`
  let interactive: BrowserWindow | null = null
  let companion: BrowserWindow | null = null
  const backgrounds = new Set<BrowserWindow>()
  const displayBoundsByWindow = new WeakMap<BrowserWindow, Electron.Rectangle>()
  let revision = 0
  let stageEpoch = 0
  let stageVisible = true
  let foreground = false
  let stageAvailable = false
  let fullscreen = false
  let compatibilityWarning: string | null = null

  const activityBySource: Record<
    'host' | 'desktop',
    { state: DesktopCompanionActivityState; voicePreparing: boolean }
  > = {
    host: { state: 'idle', voicePreparing: false },
    desktop: { state: 'idle', voicePreparing: false }
  }

  let stageInsets: DesktopStageInsets = { top: 48, bottom: 96, left: 16, right: 16 }
  let effectiveMode: PresentationMode = 'window'
  let status: PresentationState['status'] = 'inactive'
  let notifiedMode: PresentationMode | undefined
  let notifiedStatus: PresentationState['status'] | undefined
  let failureReason: string | null = null
  let hostReady = false
  let suppressAutoRestore = false
  let lastHeartbeat = 0
  let lastStageHeartbeat = 0
  let startupGeneration = 0
  const readyWindows = new Map<BrowserWindow, { resolve: () => void; reject: (error: Error) => void }>()
  let initialized = false
  let displayTimer: ReturnType<typeof setTimeout> | undefined
  let healthTimer: ReturnType<typeof setInterval> | undefined
  let lastActivity: StageActivity = { locked: false, idleSeconds: -1, effectiveTier: 'normal', focus: null }
  let currentBackground: DesktopBackground = { image: null, theme: 'day-clear', reduceMotion: false }
  let runningApplications: RunningApplicationsState = { status: 'inactive', error: null, windows: [] }
  const applicationListeners = new Set<(state: RunningApplicationsState) => void>()
  const playbackClaims = createPlaybackClaims()

  const rituals = new Map<
    string,
    { epoch: number; resolve: (completed: boolean) => void; timer: ReturnType<typeof setTimeout> }
  >()

  const native = createExplorerDesktopHost({
    helperPath: options.helperPath,
    journalPath: journalFile,
    log: options.log,
    onForegroundChanged: state => {
      if (foreground === state.active && stageAvailable === state.stageAvailable && fullscreen === state.fullscreen) {
        return
      }

      foreground = state.active
      stageAvailable = state.stageAvailable
      fullscreen = state.fullscreen

      if (!stageAvailable) {
        clearRituals()
      }

      publish()
    },
    onWarning: reason => {
      compatibilityWarning = reason
      publish()
    },
    onApplicationsChanged: state => {
      if (status === 'starting' || status === 'active') {
        setRunningApplications(
          state.status === 'unavailable' ? { ...state, windows: runningApplications.windows } : state
        )
      }
    },
    onFailure: reason => {
      if (status === 'inactive' || status === 'failed') {
        return
      }

      scheduleRecovery(reason)
    }
  })

  function setRunningApplications(state: RunningApplicationsState): void {
    runningApplications = state

    for (const listener of applicationListeners) {
      listener(state)
    }
  }

  function snapshot(): PresentationState {
    const allDisplays = app.isReady() ? screen.getAllDisplays() : []

    const displays = allDisplays.map(display => ({
      id: display.id,
      label: display.label || `显示器 ${display.id}`,
      width: display.bounds.width,
      height: display.bounds.height
    }))

    const preferred = preferences.get()

    const actual =
      effectiveMode === 'desktop' && interactive && !interactive.isDestroyed()
        ? screen.getDisplayMatching(interactive.getBounds())
        : null

    const targetDisplay =
      actual ??
      allDisplays.find(display => display.id === preferred.displayId) ??
      (app.isReady() ? screen.getPrimaryDisplay() : null)

    const desktopUnlocked =
      (foreground || stageAvailable) && effectiveMode === 'desktop' && powerMonitor.getSystemIdleState(1) !== 'locked'

    return {
      requestedMode: preferred.mode,
      effectiveMode,
      status,
      failureReason,
      supported: process.platform === 'win32',
      foreground: foreground && desktopUnlocked,
      stageAvailable: stageAvailable && desktopUnlocked,
      fullscreen,
      companionAlwaysOnTop: preferred.companionAlwaysOnTop,
      stageInsets: { ...stageInsets },
      compatibilityWarning,
      companionActivity:
        DESKTOP_COMPANION_ACTIVITY_PRIORITY[activityBySource.host.state] >
        DESKTOP_COMPANION_ACTIVITY_PRIORITY[activityBySource.desktop.state]
          ? activityBySource.host.state
          : activityBySource.desktop.state,
      voicePreparing: activityBySource.host.voicePreparing || activityBySource.desktop.voicePreparing,
      displayId: targetDisplay?.id ?? null,
      displays,
      wallpaperTarget: targetDisplay
        ? {
            width: Math.round(targetDisplay.bounds.width * targetDisplay.scaleFactor),
            height: Math.round(targetDisplay.bounds.height * targetDisplay.scaleFactor)
          }
        : null,
      stageOwner: effectiveMode === 'desktop' ? 'desktop' : 'sprite',
      stageVisible,
      stageEpoch,
      revision
    }
  }

  function canUseStage(): boolean {
    return (
      effectiveMode === 'desktop' &&
      status === 'active' &&
      stageVisible &&
      stageAvailable &&
      powerMonitor.getSystemIdleState(1) !== 'locked'
    )
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

    sendToWindow(companion, IPC.event.presentationRitualCancelled, { callId, epoch: ritual.epoch })
    ritual.resolve(false)
  }

  function clearRituals(): void {
    for (const callId of rituals.keys()) {
      cancelRitual(callId)
    }
  }

  function destroyWindows(): void {
    readyWindows.clear()
    activityBySource.desktop = { state: 'idle', voicePreparing: false }

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

    if (companion && !companion.isDestroyed()) {
      companion.destroy()
    }

    companion = null
    stageAvailable = false
    fullscreen = false
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
    setRunningApplications({ status: 'inactive', error: null, windows: [] })
    clearRituals()
    stageEpoch += 1
    playbackClaims.reset()
    publish()

    const recording = reason
      ? Promise.resolve()
          .then(() => atomicWriteFile(mainInterruptedFile, 'main_failure'))
          .catch(error => {
            const message = `[desktop] interruption marker write: ${errorMessage(error)}`

            try {
              options.log(message)
            } catch {
              console.warn(message)
            }
          })
      : Promise.resolve()

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
      try {
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
      } finally {
        await recording
      }
    }
  }

  function makeWindow(
    display: Electron.Display,
    role: 'desktop' | 'desktop-background' | 'desktop-companion'
  ): BrowserWindow {
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
      title: role === 'desktop' ? '唤生桌面' : role === 'desktop-companion' ? '唤生伙伴' : '唤生背景',
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

    options.lockZoom(win)

    win.setBounds(display.bounds)
    displayBoundsByWindow.set(win, { ...display.bounds })

    if (primary) {
      win.setIgnoreMouseEvents(true, { forward: true })
    }

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

    win.on('closed', () => {
      if (status === 'starting' || status === 'active') {
        scheduleRecovery('桌面窗口已关闭，已恢复系统桌面。')
      }
    })

    return win
  }

  async function loadWindow(
    win: BrowserWindow,
    role: 'desktop' | 'desktop-background' | 'desktop-companion'
  ): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined

    const loading = new Promise<void>((resolve, reject) => {
      const ready = new Promise<void>(reportReady => {
        readyWindows.set(win, { resolve: reportReady, reject })
      })

      timer = setTimeout(() => reject(new Error('桌面界面准备超时。')), 15000)
      void Promise.all([ready, win.loadURL(options.rendererUrlFor(role, options.seedTheme()))]).then(
        () => resolve(),
        reject
      )
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
    setRunningApplications({ status: 'loading', error: null, windows: [] })
    failureReason = null
    compatibilityWarning = null
    stageEpoch += 1
    publish()

    try {
      const allDisplays = screen.getAllDisplays()

      const selected =
        allDisplays.find(display => display.id === preferences.get().displayId) ?? screen.getPrimaryDisplay()

      await options.closeSurfaces()
      assertAttempt()
      interactive = makeWindow(selected, 'desktop')
      const main = interactive
      await loadWindow(main, 'desktop')
      companion = makeWindow(selected, 'desktop-companion')
      const stage = companion
      await loadWindow(stage, 'desktop-companion')
      assertAttempt()

      for (const display of allDisplays) {
        const win = makeWindow(display, 'desktop-background')
        backgrounds.add(win)
      }

      await Promise.all([...backgrounds].map(win => loadWindow(win, 'desktop-background')))

      const windows = [...backgrounds, stage, main]

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
        companionAlwaysOnTop: preferences.get().companionAlwaysOnTop,
        workArea: screen.dipToScreenRect(main, {
          ...selected.bounds,
          y: selected.bounds.y + 44,
          height: selected.bounds.height - 44 - 96
        }),
        windows: windows.map(win => {
          const bounds = displayBoundsByWindow.get(win)

          if (!bounds) {
            throw new Error('桌面窗口缺少显示器边界。')
          }

          return {
            handle: win.getNativeWindowHandle(),
            bounds: screen.dipToScreenRect(win, bounds),
            role: win === main ? ('overlay' as const) : win === stage ? ('companion' as const) : ('background' as const)
          }
        })
      })

      assertReady()

      for (const win of windows) {
        if (fullscreen && (win === main || (win === stage && preferences.get().companionAlwaysOnTop))) {
          win.hide()
        } else {
          win.showInactive()
        }
      }

      assertReady()

      effectiveMode = 'desktop'
      status = 'active'
      lastHeartbeat = Date.now()
      options.getSpriteWindow()?.hide()
      lastStageHeartbeat = Date.now()
      publish()
      healthTimer = setInterval(() => {
        if (Date.now() - Math.min(lastHeartbeat, lastStageHeartbeat) > 10000) {
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
    let interrupted = existsSync(interruptedFile) || existsSync(mainInterruptedFile)
    suppressAutoRestore = interrupted

    if (process.platform === 'win32' && existsSync(journalFile)) {
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

    // 残留 guardian 在上个主进程退出后才写 .interrupted，且与 recover() 争用同一恢复锁；首次检查可能早于其写入，recover 后须复检。
    interrupted ||= existsSync(interruptedFile) || existsSync(mainInterruptedFile)
    suppressAutoRestore ||= interrupted

    if (interrupted && !failureReason) {
      failureReason = '上次桌面异常中断，已保留窗口模式。请重新选择桌面模式。'
      status = 'failed'
    }

    if (interrupted && !existsSync(journalFile)) {
      for (const marker of [interruptedFile, mainInterruptedFile]) {
        if (existsSync(marker)) {
          try {
            unlinkSync(marker)
          } catch (error) {
            options.log(`[desktop] interruption marker cleanup: ${errorMessage(error)}`)
          }
        }
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
  const isStageSender = (sender: Pick<WebContents, 'id'>): boolean => isSenderWindow(sender, companion)

  function assertStage(sender: WebContents): void {
    if (!isStageSender(sender)) {
      throw new Error('仅桌面精灵允许此操作。')
    }
  }

  function assertDesktop(sender: WebContents): void {
    if (!isDesktopSender(sender)) {
      throw new Error('仅桌面入口允许此操作。')
    }
  }

  function captureDesktopEligibility(sender: WebContents, epoch = stageEpoch): () => boolean {
    assertDesktop(sender)
    const generation = startupGeneration
    const identity = options.authIdentity()

    return () =>
      status === 'active' &&
      effectiveMode === 'desktop' &&
      generation === startupGeneration &&
      epoch === stageEpoch &&
      !lastActivity.locked &&
      powerMonitor.getSystemIdleState(1) !== 'locked' &&
      options.authenticated() &&
      identity === options.authIdentity() &&
      isDesktopSender(sender)
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

          if (effectiveMode === 'desktop' && status === 'active' && existsSync(mainInterruptedFile)) {
            try {
              unlinkSync(mainInterruptedFile)
            } catch (error) {
              options.log(`[desktop] main interruption marker cleanup: ${errorMessage(error)}`)
            }
          }
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
    ipcMain.handle(IPC.invoke.presentationCompanionActivity, (event, raw: unknown) => {
      const source = isDesktopSender(event.sender)
        ? 'desktop'
        : isSenderWindow(event.sender, options.getSpriteWindow())
          ? 'host'
          : null

      const activity = raw as { state?: unknown; voicePreparing?: unknown; authSessionId?: unknown } | null

      if (
        !source ||
        typeof activity?.state !== 'string' ||
        !Object.hasOwn(DESKTOP_COMPANION_ACTIVITY_PRIORITY, activity.state) ||
        typeof activity.voicePreparing !== 'boolean'
      ) {
        throw new Error('无效伙伴活动。')
      }

      if (!options.authenticated() || activity.authSessionId !== options.authIdentity()) {
        return
      }

      const state = activity.state as DesktopCompanionActivityState

      if (
        activityBySource[source].state === state &&
        activityBySource[source].voicePreparing === activity.voicePreparing
      ) {
        return
      }

      activityBySource[source] = { state, voicePreparing: activity.voicePreparing }
      publish()
    })
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
      assertStage(event.sender)

      return lastActivity
    })
    ipcMain.handle(IPC.invoke.presentationSetStageLayout, (event, raw: unknown) => {
      assertDesktop(event.sender)
      const layout = raw as { visible?: unknown; insets?: Partial<DesktopStageInsets> } | null
      const insets = layout?.insets

      if (
        typeof layout?.visible !== 'boolean' ||
        !insets ||
        ![insets.top, insets.bottom, insets.left, insets.right].every(
          value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10000
        )
      ) {
        throw new Error('无效舞台状态。')
      }

      if (
        stageVisible === layout.visible &&
        stageInsets.top === insets.top &&
        stageInsets.bottom === insets.bottom &&
        stageInsets.left === insets.left &&
        stageInsets.right === insets.right
      ) {
        return
      }

      stageVisible = layout.visible
      stageInsets = { top: insets.top!, bottom: insets.bottom!, left: insets.left!, right: insets.right! }

      if (!stageVisible) {
        clearRituals()
      }

      publish()
    })
    ipcMain.handle(IPC.invoke.presentationSetIgnoreMouseEvents, (event, payload: unknown) => {
      const win = isDesktopSender(event.sender) ? interactive : isStageSender(event.sender) ? companion : null
      const value = payload as { ignore?: unknown; forward?: unknown } | null

      if (
        !win ||
        typeof value?.ignore !== 'boolean' ||
        (value.forward !== undefined && typeof value.forward !== 'boolean')
      ) {
        throw new Error('无效桌面鼠标捕获。')
      }

      win.setIgnoreMouseEvents(value.ignore, { forward: value.forward === true })
    })
    ipcMain.handle(IPC.invoke.presentationSetCompanionTopmost, (event, enabled: unknown) => {
      assertDesktop(event.sender)

      if (typeof enabled !== 'boolean') {
        throw new Error('无效精灵层级。')
      }

      return serial(async () => {
        assertDesktop(event.sender)
        const previous = preferences.get().companionAlwaysOnTop

        if (previous === enabled) {
          return snapshot()
        }

        try {
          await native.setCompanionAlwaysOnTop(enabled)
        } catch (error) {
          scheduleRecovery(`精灵层级切换失败：${errorMessage(error)}`)
          throw error
        }

        try {
          await preferences.set({ companionAlwaysOnTop: enabled })
        } catch (error) {
          try {
            await native.setCompanionAlwaysOnTop(previous)
          } catch (rollbackError) {
            scheduleRecovery(`精灵层级恢复失败：${errorMessage(rollbackError)}`)
          }

          throw error
        }

        stageEpoch += 1
        clearRituals()
        publish()

        return snapshot()
      })
    })
    ipcMain.handle(IPC.invoke.presentationCompanionInteraction, (event, raw: unknown) => {
      assertStage(event.sender)
      const value = raw as { kind?: unknown; x?: unknown; y?: unknown; paths?: unknown } | null
      let interaction: DesktopCompanionInteraction

      if (value?.kind === 'hide' || value?.kind === 'toggle-whisper') {
        interaction = { kind: value.kind }
      } else if (
        value?.kind === 'menu' &&
        typeof value.x === 'number' &&
        Number.isFinite(value.x) &&
        typeof value.y === 'number' &&
        Number.isFinite(value.y)
      ) {
        interaction = { kind: 'menu', x: value.x, y: value.y }
      } else if (
        value?.kind === 'drop' &&
        Array.isArray(value.paths) &&
        value.paths.length <= 100 &&
        value.paths.every(p => typeof p === 'string' && p.length <= 32768)
      ) {
        interaction = { kind: 'drop', paths: value.paths }
      } else {
        throw new Error('无效精灵交互。')
      }

      const identity = options.authIdentity()
      const epoch = stageEpoch

      return serial(async () => {
        const eligible = (): boolean =>
          isStageSender(event.sender) &&
          status === 'active' &&
          !fullscreen &&
          stageEpoch === epoch &&
          options.authenticated() &&
          identity === options.authIdentity() &&
          powerMonitor.getSystemIdleState(1) !== 'locked'

        if (!eligible() || !interactive) {
          return
        }

        if (interaction.kind !== 'hide' && !(await native.focus(interactive.getNativeWindowHandle(), eligible))) {
          return
        }

        if (eligible()) {
          sendToWindow(interactive, IPC.event.companionInteraction, interaction)
        }
      })
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
      if (!isDesktopSender(event.sender) && !isStageSender(event.sender)) {
        throw new Error('无效桌面窗口。')
      }

      const win = BrowserWindow.fromWebContents(event.sender)

      if (win) {
        readyWindows.get(win)?.resolve()
      }

      lastHeartbeat = Date.now()
    })
    ipcMain.handle(IPC.invoke.presentationFocus, (event, epoch: unknown) => {
      assertDesktop(event.sender)

      if (typeof epoch !== 'number' || !Number.isSafeInteger(epoch)) {
        throw new Error('无效桌面代次。')
      }

      const eligible = captureDesktopEligibility(event.sender, epoch)

      return serial(async () => {
        assertDesktop(event.sender)

        if (!eligible()) {
          return false
        }

        return native.focus(interactive!.getNativeWindowHandle(), eligible)
      })
    })
    ipcMain.handle(IPC.invoke.presentationHeartbeat, event => {
      if (isDesktopSender(event.sender)) {
        lastHeartbeat = Date.now()
      } else if (isStageSender(event.sender)) {
        lastStageHeartbeat = Date.now()
      } else {
        throw new Error('无效桌面心跳。')
      }
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
      assertStage(event.sender)

      if (!canUseStage()) {
        return false
      }

      return playbackClaims.claim(raw)
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

      sendToWindow(companion, IPC.event.presentationStageActivity, activity)
    })
    ipcMain.handle(IPC.invoke.presentationRitualRequest, (event, raw: Omit<StageRitualRequest, 'epoch'>) => {
      if (!isSenderWindow(event.sender, options.getSpriteWindow())) {
        throw new Error('仅精灵宿主允许请求仪式。')
      }

      if (
        !canUseStage() ||
        !companion ||
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
        sendToWindow(companion, IPC.event.presentationRitual, { ...raw, epoch: stageEpoch })
      })
    })
    ipcMain.handle(
      IPC.invoke.presentationRitualComplete,
      (event, reply: { callId: string; epoch: number; completed: boolean }) => {
        assertStage(event.sender)
        const pending = reply && rituals.get(reply.callId)

        if (!pending || reply.epoch !== pending.epoch || reply.epoch !== stageEpoch) {
          return
        }

        clearTimeout(pending.timer)
        rituals.delete(reply.callId)
        pending.resolve(reply.completed === true && canUseStage())
      }
    )
  }

  return {
    captureApplicationEligibility: captureDesktopEligibility,
    getRunningApplications: () => runningApplications,
    onRunningApplicationsChanged: (listener: (state: RunningApplicationsState) => void): (() => void) => {
      applicationListeners.add(listener)

      return () => applicationListeners.delete(listener)
    },
    refreshApplications: (sender: WebContents): Promise<void> => {
      const eligible = captureDesktopEligibility(sender)

      return serial(() => native.refreshApplications(eligible))
    },
    activateExternal: (sender: WebContents, windowId: string): Promise<void> => {
      const eligible = captureDesktopEligibility(sender)

      return serial(() => native.activateExternal(windowId, eligible))
    },
    closeExternal: (sender: WebContents, windowIds: string[]): Promise<void> => {
      const eligible = captureDesktopEligibility(sender)

      return serial(() => native.closeExternal(windowIds, eligible))
    },
    getState: snapshot,
    setMode,
    getWindow: () => interactive,
    getStageWindow: () => companion,
    isDesktopSender,
    isStageSender,
    registerIpc,
    initialize,
    stop: () => {
      invalidateStartup('桌面准备已取消。')

      return serial(() => leave())
    },
    accountChanged: () => {
      activityBySource.host = { state: 'idle', voicePreparing: false }
      activityBySource.desktop = { state: 'idle', voicePreparing: false }
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

      const win = interactive
      const eligible = captureDesktopEligibility(win.webContents)

      void serial(async () => {
        if (!eligible() || fullscreen) {
          return
        }

        win.focus()
        win.moveTop()
        await native.focus(win.getNativeWindowHandle(), () => eligible() && !fullscreen)
      }).catch(error => options.log(`[desktop] navigation activation: ${errorMessage(error)}`))

      return true
    }
  }
}

export type DesktopPresentation = ReturnType<typeof createDesktopPresentation>
