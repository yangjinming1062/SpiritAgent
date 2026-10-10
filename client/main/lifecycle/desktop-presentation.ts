import { existsSync, unlinkSync } from 'node:fs'
import path from 'node:path'

import { app, BrowserWindow, type IpcMain, powerMonitor, screen, type WebContents } from 'electron'

import {
  DESKTOP_COMPANION_ACTIVITY_PRIORITY,
  type DesktopBackgroundPlayback,
  type DesktopBackgroundRequest,
  type DesktopCompanionActivityState,
  type DesktopMediaBytes,
  type DesktopMediaReference,
  type DesktopNavigation,
  IPC,
  type PresentationMode,
  type PresentationState,
  type StageActivity
} from '@ipc/contracts'

import { isSenderWindow } from '../security/ipc-trust'
import type { RunningApplicationsState } from '../shared/desktop-applications'
import { atomicWriteFile, broadcastToAllWindows, createSerialQueue, errorMessage, sendToWindow } from '../shared/utils'

import { emptyDesktopBackground, parseDesktopBackgroundRequest } from './desktop-background'
import { createExplorerDesktopHost } from './explorer-desktop-host'
import { createPresentationPreferences } from './presentation-preferences'

interface DesktopPresentationOptions {
  userData: string
  preloadPath: string
  backgroundPreloadPath: string
  helperPath: string
  developmentPreparationError?: string
  rendererUrlFor: (role: 'desktop' | 'desktop-background', theme?: string) => string
  seedTheme: () => string | undefined
  getSpriteWindow: () => BrowserWindow | null
  isSettingsSender: (sender: WebContents) => boolean
  closeSurfaces: () => Promise<void>
  restoreSprite: () => void
  authenticated: () => boolean
  authIdentity: () => string | null
  loadMedia: (sender: WebContents, reference: DesktopMediaReference) => Promise<DesktopMediaBytes>
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
  const backgrounds = new Set<BrowserWindow>()
  const displayBoundsByWindow = new WeakMap<BrowserWindow, Electron.Rectangle>()
  let revision = 0
  let stageEpoch = 0
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

  let effectiveMode: PresentationMode = 'window'
  let prepareDesktopMedia = false
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
  let accountEpoch = 0
  let backgroundGeneration = 0
  let backgroundRevision = 0
  let requestedBackground: DesktopBackgroundRequest | null = null
  let loadedBackground: DesktopBackgroundRequest | null = null
  let currentBackground = emptyDesktopBackground(accountEpoch, backgroundRevision)
  const dispatchedPlays = new Map<string, { accountEpoch: number; revision: number }>()
  let runningApplications: RunningApplicationsState = { status: 'inactive', error: null, windows: [] }
  const applicationListeners = new Set<(state: RunningApplicationsState) => void>()

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
      prepareDesktopMedia,
      failureReason,
      supported: process.platform === 'win32',
      foreground: foreground && desktopUnlocked,
      stageAvailable: stageAvailable && desktopUnlocked,
      fullscreen,
      compatibilityWarning,
      companionActivity:
        DESKTOP_COMPANION_ACTIVITY_PRIORITY[activityBySource.host.state] >
        DESKTOP_COMPANION_ACTIVITY_PRIORITY[activityBySource.desktop.state]
          ? activityBySource.host.state
          : activityBySource.desktop.state,
      voicePreparing: activityBySource.host.voicePreparing || activityBySource.desktop.voicePreparing,
      displayId: targetDisplay?.id ?? null,
      displays,
      stageOwner: effectiveMode === 'desktop' ? 'desktop' : 'sprite',
      stageVisible: effectiveMode !== 'desktop',
      stageEpoch,
      revision
    }
  }

  function backgroundPaused(): boolean {
    return (
      status !== 'active' ||
      requestedBackground?.paused === true ||
      currentBackground.reduceMotion ||
      fullscreen ||
      lastActivity.locked ||
      powerMonitor.getSystemIdleState(1) === 'locked'
    )
  }

  function sendBackground(): void {
    for (const win of backgrounds) {
      sendToWindow(win, IPC.event.backgroundMedia, currentBackground)
    }
  }

  function publishBackgroundState(): void {
    const paused = backgroundPaused()

    if (currentBackground.paused !== paused) {
      currentBackground = { ...currentBackground, paused }
      sendBackground()
    }
  }

  function interruptBackground(): void {
    for (const [playId, dispatched] of dispatchedPlays) {
      sendToWindow(interactive, IPC.event.backgroundPlayback, { ...dispatched, playId, status: 'interrupted' })
    }

    dispatchedPlays.clear()
    currentBackground = { ...currentBackground, paused: true }
    sendBackground()
  }

  async function setBackground(sender: WebContents, raw: unknown): Promise<void> {
    assertDesktop(sender)
    const request = parseDesktopBackgroundRequest(raw)
    const identity = options.authIdentity()
    const epoch = stageEpoch
    const generation = ++backgroundGeneration

    const valid = (): boolean =>
      generation === backgroundGeneration &&
      epoch === stageEpoch &&
      identity === options.authIdentity() &&
      options.authenticated() &&
      isDesktopSender(sender) &&
      (status === 'starting' || status === 'active')

    if (request.authSessionId !== identity || !valid()) {
      // 换号与代次更替属预期竞态，记日志后仍须失败返回，避免把未应用的背景伪装成成功。
      options.log(`[desktop] dropped stale background request (playId=${String(request.playId)})`)
      throw new Error('桌面背景请求已失效。')
    }

    if (
      request.playId !== currentBackground.playId &&
      request.expiresAt &&
      Date.parse(request.expiresAt) <= Date.now()
    ) {
      throw new Error('桌面播放请求已过期。')
    }

    if (request.clear) {
      requestedBackground = request
      interruptBackground()
      loadedBackground = null
      currentBackground = {
        ...emptyDesktopBackground(accountEpoch, ++backgroundRevision, request.theme),
        reduceMotion: request.reduceMotion
      }
      sendBackground()

      return
    }

    const sameMedia = (a: DesktopMediaReference | null, b: DesktopMediaReference | null): boolean =>
      a?.url === b?.url && a?.contentHash === b?.contentHash

    const unchanged =
      loadedBackground &&
      request.playId === loadedBackground.playId &&
      request.setEpoch === loadedBackground.setEpoch &&
      sameMedia(request.video, loadedBackground.video) &&
      sameMedia(request.poster, loadedBackground.poster)

    let video = currentBackground.video
    let poster = currentBackground.poster

    if (!unchanged) {
      try {
        ;[video, poster] = await Promise.all([
          request.video ? options.loadMedia(sender, request.video) : null,
          request.poster ? options.loadMedia(sender, request.poster) : null
        ])

        if (video && !/^video\/mp4(?:;|$)/i.test(video.mime)) {
          throw new Error('桌面视频格式不可用。')
        }

        if (poster && !/^image\/(?:png|jpeg|webp|gif)(?:;|$)/i.test(poster.mime)) {
          throw new Error('桌面封面格式不可用。')
        }
      } catch (error) {
        if (valid() && request.playId) {
          sendToWindow(interactive, IPC.event.backgroundPlayback, {
            accountEpoch,
            revision: ++backgroundRevision,
            playId: request.playId,
            status: 'failed',
            error: errorMessage(error).slice(0, 2000)
          })
        }

        throw error
      }
    }

    if (!valid()) {
      options.log(`[desktop] dropped stale background request after load (playId=${String(request.playId)})`)
      throw new Error('桌面背景请求已失效。')
    }

    if (
      request.playId !== currentBackground.playId &&
      request.expiresAt &&
      Date.parse(request.expiresAt) <= Date.now()
    ) {
      throw new Error('桌面播放请求已过期。')
    }

    requestedBackground = request

    const { authSessionId: _authSessionId, video: _video, poster: _poster, ...settings } = request
    currentBackground = {
      ...settings,
      accountEpoch,
      revision: unchanged ? currentBackground.revision : ++backgroundRevision,
      video,
      poster
    }
    currentBackground.paused = backgroundPaused()
    loadedBackground = request

    if (request.playId && video) {
      dispatchedPlays.set(request.playId, { accountEpoch, revision: currentBackground.revision })
    }

    sendBackground()
  }

  function publish(): void {
    revision += 1
    broadcastToAllWindows(IPC.event.presentationChanged, snapshot())
    publishBackgroundState()

    if (notifiedMode !== effectiveMode || notifiedStatus !== status) {
      notifiedMode = effectiveMode
      notifiedStatus = status
      options.onModeChanged?.()
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

    stageAvailable = false
    fullscreen = false
    backgroundGeneration += 1
    requestedBackground = null
    loadedBackground = null
    dispatchedPlays.clear()
    currentBackground = emptyDesktopBackground(accountEpoch, ++backgroundRevision, options.seedTheme())
  }

  function scheduleRecovery(reason: string): void {
    if (status === 'inactive' || status === 'failed') {
      return
    }

    invalidateStartup(reason)
    foreground = false
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

  async function leave(reason?: string, preservePreparation = false): Promise<void> {
    let finalReason = reason
    prepareDesktopMedia &&= preservePreparation
    clearTimeout(displayTimer)
    clearInterval(healthTimer)
    healthTimer = undefined
    status = 'recovering'
    setRunningApplications({ status: 'inactive', error: null, windows: [] })
    stageEpoch += 1
    backgroundGeneration += 1
    interruptBackground()
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

  async function loadWindow(win: BrowserWindow, role: 'desktop' | 'desktop-background'): Promise<void> {
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

    if (options.developmentPreparationError) {
      throw new Error(`Windows 桌面组件准备失败，请修复工具链并重启开发客户端：${options.developmentPreparationError}`)
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
      assertAttempt()
      const background = makeWindow(selected, 'desktop-background')
      backgrounds.add(background)
      await loadWindow(background, 'desktop-background')
      const windows = [background, main]

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
            role: win === main ? ('overlay' as const) : ('background' as const)
          }
        })
      })

      assertReady()

      for (const win of windows) {
        if (fullscreen && win === main) {
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

            await leave(undefined, true)

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
      prepareDesktopMedia = mode === 'desktop'
      publish()

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
    ipcMain.handle(IPC.invoke.presentationSetIgnoreMouseEvents, (event, payload: unknown) => {
      const win = isDesktopSender(event.sender) ? interactive : null
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
          await leave(undefined, true)

          if (currentGeneration === startupGeneration) {
            await enter()
          }
        }

        publish()

        return snapshot()
      })
    })
    ipcMain.handle(IPC.invoke.presentationReportReady, event => {
      if (!isDesktopSender(event.sender)) {
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
    ipcMain.handle(IPC.invoke.presentationSetBackground, (event, raw: unknown) => setBackground(event.sender, raw))
    ipcMain.handle(IPC.invoke.backgroundReady, event => {
      const win = [...backgrounds].find(candidate => isSenderWindow(event.sender, candidate))

      if (!win) {
        throw new Error('无效背景窗口。')
      }

      sendToWindow(win, IPC.event.backgroundMedia, currentBackground)
      readyWindows.get(win)?.resolve()
    })
    ipcMain.handle(IPC.invoke.backgroundAcknowledge, (event, raw: unknown) => {
      if (![...backgrounds].some(win => isSenderWindow(event.sender, win))) {
        throw new Error('无效背景窗口。')
      }

      const value = raw as Partial<DesktopBackgroundPlayback> | null
      const dispatched = typeof value?.playId === 'string' ? dispatchedPlays.get(value.playId) : null

      if (
        !dispatched ||
        value?.accountEpoch !== accountEpoch ||
        value.revision !== dispatched.revision ||
        !['first-frame', 'started', 'completed', 'interrupted', 'failed'].includes(String(value.status)) ||
        (value.error !== undefined && (typeof value.error !== 'string' || value.error.length > 2000))
      ) {
        return
      }

      sendToWindow(interactive, IPC.event.backgroundPlayback, value as DesktopBackgroundPlayback)

      if (value.status === 'completed' || value.status === 'interrupted' || value.status === 'failed') {
        dispatchedPlays.delete(value.playId!)
      }
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

      publishBackgroundState()
    })
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
    isDesktopSender,
    registerIpc,
    initialize,
    stop: () => {
      invalidateStartup('桌面准备已取消。')

      return serial(() => leave())
    },
    accountChanged: () => {
      prepareDesktopMedia = false
      accountEpoch += 1
      backgroundGeneration += 1
      currentBackground = emptyDesktopBackground(accountEpoch, ++backgroundRevision, options.seedTheme())
      dispatchedPlays.clear()
      sendBackground()
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
