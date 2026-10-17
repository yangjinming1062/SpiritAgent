import { existsSync, unlinkSync } from 'node:fs'
import path from 'node:path'

import { app, BrowserWindow, type IpcMain, powerMonitor, screen, type WebContents } from 'electron'

import {
  type AppNavigationTarget,
  DESKTOP_COMPANION_ACTIVITY_PRIORITY,
  type DesktopCompanionActivityState,
  type DesktopMediaBytes,
  type DesktopMediaReference,
  type DesktopNavigation,
  IPC,
  type PresentationMode,
  type PresentationState,
  type StageActivity
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { DESKTOP_LAYOUT } from '../../shared/desktop-layout'
import { registerDesktopPresentationIpc } from '../ipc/desktop-presentation'
import { createUiStateBridge } from '../ipc/ui-state'
import { isSenderWindow } from '../security/ipc-trust'
import type { ApplicationEligibility, RunningApplicationsState } from '../shared/desktop-applications'
import { atomicWriteFile, broadcastToAllWindows, createSerialQueue, sendToWindow } from '../shared/utils'

import { createDesktopBackgroundRuntime } from './desktop-background-runtime'
import { readNativeDisplayBounds } from './desktop-display-resolutions'
import { createDesktopHealth } from './desktop-health'
import { createDesktopWindows } from './desktop-windows'
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
  getMainWindow: () => BrowserWindow | null
  restoreMain: (target: AppNavigationTarget) => Promise<void>
  isSettingsSender: (sender: WebContents) => boolean
  closeSurfaces: () => Promise<void>
  restoreSprite: () => void
  authenticated: () => boolean
  authIdentity: () => string | null
  authAccountId: () => string | null
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
  let startupGeneration = 0
  let initialized = false
  let disposed = false
  let desktopRendererHealthy = false
  let activeDisplay: Electron.Display | null = null
  let nativeWorkArea: PresentationState['workArea'] = null
  let nativeWorkAreaEpoch: number | null = null
  let disposal: Promise<void> | null = null
  let disposeIpc: (() => void) | null = null
  let lastActivity: StageActivity = { locked: false, idleSeconds: -1, effectiveTier: 'normal', focus: null }
  let runningApplications: RunningApplicationsState = { status: 'inactive', error: null, windows: [] }
  const applicationListeners = new Set<(state: RunningApplicationsState) => void>()
  let displayResolutionGeneration = 0
  const displayResolutions = new Map<number, { geometry: string; width: number; height: number }>()

  const windows = createDesktopWindows({
    preloadPath: options.preloadPath,
    backgroundPreloadPath: options.backgroundPreloadPath,
    rendererUrlFor: options.rendererUrlFor,
    seedTheme: options.seedTheme,
    lockZoom: options.lockZoom,
    installWindowHandlers: options.installWindowHandlers,
    onInteraction: () => {
      if (!disposed && status === 'active') {
        void native
          .cancelExternalInteraction()
          .catch(error => options.log(`[desktop] cancelling external interaction: ${errorMessage(error)}`))
      }
    },
    onFailure: reason => {
      if (status === 'starting' || status === 'active') {
        scheduleRecovery(reason)
      }
    }
  })

  const isDesktopSender = windows.isDesktopSender

  const uiState = createUiStateBridge({
    accountId: options.authAccountId,
    authIdentity: options.authIdentity,
    isViewSender: sender => isDesktopSender(sender) || isSenderWindow(sender, options.getMainWindow()),
    isInputSender: sender =>
      isDesktopSender(sender) ||
      isSenderWindow(sender, options.getMainWindow()) ||
      isSenderWindow(sender, options.getSpriteWindow()),
    isActiveSender: sender =>
      !['starting', 'recovering'].includes(status) &&
      (effectiveMode === 'desktop' ? isDesktopSender(sender) : isSenderWindow(sender, options.getMainWindow())),
    onBusyChanged: () => {
      publish()
      options.onModeChanged?.()
    }
  })

  const health = createDesktopHealth({
    onDisplaysChanged,
    onLock: () => {
      foreground = false
      publish()
    },
    onUnlock: publish,
    onSuspend: () => scheduleRecovery('电脑已进入休眠，已恢复窗口模式。'),
    onResume: () => scheduleRecovery('电脑已从休眠恢复，已恢复窗口模式。'),
    onHeartbeatLost: () => scheduleRecovery('桌面连接中断，已恢复系统桌面。')
  })

  const background = createDesktopBackgroundRuntime({
    authIdentity: options.authIdentity,
    assertDesktop,
    captureEligibility: sender => {
      const epoch = stageEpoch
      const identity = options.authIdentity()
      const generation = startupGeneration

      return () =>
        !disposed &&
        generation === startupGeneration &&
        epoch === stageEpoch &&
        identity === options.authIdentity() &&
        options.authenticated() &&
        isDesktopSender(sender) &&
        (status === 'starting' || status === 'active')
    },
    isPaused: () =>
      status !== 'active' || fullscreen || lastActivity.locked || powerMonitor.getSystemIdleState(1) === 'locked',
    seedTheme: options.seedTheme,
    loadMedia: options.loadMedia,
    send: media => {
      for (const win of windows.getBackgrounds()) {
        sendToWindow(win, IPC.event.backgroundMedia, media)
      }
    },
    playback: playback => sendToWindow(windows.getInteractive(), IPC.event.backgroundPlayback, playback),
    log: options.log
  })

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
    onWorkAreaChanged: updateNativeWorkArea,
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
    const primaryId = app.isReady() ? screen.getPrimaryDisplay().id : null

    const displays = allDisplays.map(display => {
      const native = displayResolutions.get(display.id)
      const resolution = native?.geometry === displayGeometry(display) ? native : null

      return {
        id: display.id,
        label: display.label || `显示器 ${display.id}`,
        isPrimary: display.id === primaryId,
        width: display.bounds.width,
        height: display.bounds.height,
        pixelWidth:
          process.platform === 'win32'
            ? (resolution?.width ?? null)
            : Math.round(display.bounds.width * display.scaleFactor),
        pixelHeight:
          process.platform === 'win32'
            ? (resolution?.height ?? null)
            : Math.round(display.bounds.height * display.scaleFactor)
      }
    })

    const preferred = preferences.get()
    const activeDisplayId = activeDisplay?.id

    const targetDisplay =
      allDisplays.find(display => display.id === activeDisplayId) ?? resolveDisplay(allDisplays, preferred.displayId)

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
      workArea: effectiveMode === 'desktop' && status === 'active' ? nativeWorkArea : null,
      displays,
      stageOwner: effectiveMode === 'desktop' ? 'desktop' : 'sprite',
      stageVisible: effectiveMode !== 'desktop',
      stageEpoch,
      revision,
      switchBlockedReason: uiState.blockedReason()
    }
  }

  function updateNativeWorkArea(physical: Electron.Rectangle): void {
    const win = windows.getInteractive()

    if (
      disposed ||
      nativeWorkAreaEpoch !== stageEpoch ||
      (status !== 'starting' && status !== 'active') ||
      !activeDisplay ||
      !win ||
      win.isDestroyed()
    ) {
      return
    }

    const dip = screen.screenToDipRect(win, physical)
    const display = activeDisplay.bounds
    const x = Math.max(0, dip.x - display.x)
    const y = Math.max(0, dip.y - display.y)
    const right = Math.min(display.width, dip.x + dip.width - display.x)
    const bottom = Math.min(display.height, dip.y + dip.height - display.y)

    if (right <= x || bottom <= y) {
      throw new Error('Desktop helper work area does not intersect the interactive display')
    }

    const area = { x, y, width: right - x, height: bottom - y }

    if (
      nativeWorkArea?.x === area.x &&
      nativeWorkArea.y === area.y &&
      nativeWorkArea.width === area.width &&
      nativeWorkArea.height === area.height
    ) {
      return
    }

    nativeWorkArea = area
    publish()
  }

  function displayGeometry(display: Electron.Display): string {
    return JSON.stringify([display.bounds, display.scaleFactor, display.rotation])
  }

  async function refreshDisplayResolutions(): Promise<void> {
    const generation = ++displayResolutionGeneration
    displayResolutions.clear()

    if (process.platform !== 'win32' || disposed || !app.isReady()) {
      return
    }

    const displays = screen.getAllDisplays()
    const geometries = displays.map(displayGeometry)

    try {
      const bounds = await readNativeDisplayBounds(
        options.helperPath,
        displays.map(display => screen.dipToScreenRect(null, display.bounds))
      )

      if (disposed || generation !== displayResolutionGeneration) {
        return
      }

      for (const [index, physical] of bounds.entries()) {
        if (physical) {
          displayResolutions.set(displays[index].id, {
            geometry: geometries[index],
            width: physical.width,
            height: physical.height
          })
        }
      }
    } catch (error) {
      if (!disposed && generation === displayResolutionGeneration) {
        options.log(`[desktop] physical display resolution: ${errorMessage(error)}`)
      }
    }

    if (!disposed && generation === displayResolutionGeneration) {
      publish()
    }
  }

  function resolveDisplay(
    displays: Electron.Display[],
    preferredId: number | null,
    sender?: WebContents
  ): Electron.Display | undefined {
    const preferred = displays.find(display => display.id === preferredId)

    if (preferred || !displays.length) {
      return preferred
    }

    const source = sender ? BrowserWindow.fromWebContents(sender) : options.getMainWindow()

    const currentId =
      source && !source.isDestroyed() && (sender || source.isVisible() || source.isMinimized())
        ? screen.getDisplayMatching(source.getBounds()).id
        : screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).id

    return displays.find(display => display.id === currentId) ?? displays[0]
  }

  function sameDisplayGeometry(left: Electron.Display, right: Electron.Display): boolean {
    return (
      left.id === right.id &&
      left.scaleFactor === right.scaleFactor &&
      left.rotation === right.rotation &&
      left.bounds.x === right.bounds.x &&
      left.bounds.y === right.bounds.y &&
      left.bounds.width === right.bounds.width &&
      left.bounds.height === right.bounds.height
    )
  }

  function publish(): void {
    revision += 1
    broadcastToAllWindows(IPC.event.presentationChanged, snapshot())
    background.publish()

    if (notifiedMode !== effectiveMode || notifiedStatus !== status) {
      notifiedMode = effectiveMode
      notifiedStatus = status
      options.onModeChanged?.()
    }
  }

  function destroyWindows(): void {
    desktopRendererHealthy = false
    activityBySource.desktop = { state: 'idle', voicePreparing: false }
    windows.destroy()
    activeDisplay = null
    nativeWorkArea = null
    nativeWorkAreaEpoch = null
    stageAvailable = false
    fullscreen = false
    background.reset()
  }

  function scheduleRecovery(reason: string, recordInterruption = true): void {
    if (status === 'inactive' || status === 'failed') {
      return
    }

    invalidateStartup(reason)
    nativeWorkArea = null
    nativeWorkAreaEpoch = null
    foreground = false
    publish()

    void serial(async () => {
      if (!recordInterruption) {
        suppressAutoRestore = true

        try {
          await preferences.set({ mode: 'window' })
        } catch (error) {
          options.log(`[desktop] saving disconnected display mode: ${errorMessage(error)}`)
        }
      }

      if (status === 'inactive' || status === 'failed') {
        return
      }

      await leave(reason, false, recordInterruption)
    }).catch(error => options.log(`[desktop] recovery: ${errorMessage(error)}`))
  }

  function invalidateStartup(reason: string): void {
    startupGeneration += 1

    windows.invalidateReady(reason)
  }

  async function leave(
    reason?: string,
    preservePreparation = false,
    recordInterruption = Boolean(reason)
  ): Promise<void> {
    let finalReason = reason

    const source = windows.getInteractive()

    const captureSource =
      !disposed &&
      effectiveMode === 'desktop' &&
      status === 'active' &&
      desktopRendererHealthy &&
      source &&
      !source.isDestroyed() &&
      uiState.isOwner(source.webContents)

    prepareDesktopMedia &&= preservePreparation
    health.stopMode()
    status = 'recovering'
    nativeWorkArea = null
    nativeWorkAreaEpoch = null
    setRunningApplications({ status: 'inactive', error: null, windows: [] })
    stageEpoch += 1
    background.invalidate()
    background.interrupt()
    publish()

    if (captureSource) {
      try {
        await uiState.capture(source)
      } catch (error) {
        options.log(`[desktop] preserving last view snapshot: ${errorMessage(error)}`)
      }
    }

    if (source && !source.isDestroyed() && uiState.isOwner(source.webContents)) {
      uiState.releaseOwner()
    }

    const recording =
      recordInterruption && reason
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

        const main = options.getMainWindow()

        if (!disposed && options.authenticated() && main && !main.isDestroyed()) {
          try {
            await restoreUiState(main)
          } catch (error) {
            options.log(`[desktop] main view restore failed: ${errorMessage(error)}`)
            finalReason ??= `界面状态恢复失败：${errorMessage(error)}`
          }
        }

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

  async function enter(sender?: WebContents, targetDisplayId?: number): Promise<void> {
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
    nativeWorkArea = null
    nativeWorkAreaEpoch = null
    setRunningApplications({ status: 'loading', error: null, windows: [] })
    failureReason = null
    compatibilityWarning = null
    stageEpoch += 1
    publish()

    try {
      const allDisplays = screen.getAllDisplays()

      const preferred = preferences.get()
      const selected = resolveDisplay(allDisplays, targetDisplayId ?? preferred.displayId, sender)

      if (!selected) {
        throw new Error('当前没有可用显示器，请连接显示屏后重试。')
      }

      // 临时回退不覆盖首选屏，重新接入后仍可按原选择进入。
      if (preferred.displayId === null) {
        await preferences.set({ displayId: selected.id })
      }

      assertAttempt()
      activeDisplay = { ...selected, bounds: { ...selected.bounds } }

      const main = windows.createInteractive(selected)
      main.webContents.once('render-process-gone', () => {
        desktopRendererHealthy = false
      })
      main.once('unresponsive', () => {
        desktopRendererHealthy = false
      })
      await windows.load(main, 'desktop')
      desktopRendererHealthy = true
      await uiState.restore(main)
      assertAttempt()
      const backgroundWindow = windows.createBackground(selected)
      await windows.load(backgroundWindow, 'desktop-background')
      const surfaces = [backgroundWindow, main]

      const assertReady = (): void => {
        assertAttempt()

        if (surfaces.some(win => win.isDestroyed())) {
          throw new Error('桌面准备期间账户或窗口已失效。')
        }
      }

      assertReady()

      await options.closeSurfaces()
      options.getSpriteWindow()?.hide()
      assertReady()

      nativeWorkAreaEpoch = stageEpoch
      await native.start({
        parentPid: process.pid,
        takeover: process.env.SPIRITAGENT_DESKTOP_PROBE !== '1',
        workArea: screen.dipToScreenRect(main, {
          ...selected.bounds,
          y: selected.bounds.y,
          height: selected.bounds.height - DESKTOP_LAYOUT.bottomInset
        }),
        windows: surfaces.map(win => {
          const bounds = windows.getBounds(win)

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

      for (const win of surfaces) {
        if (fullscreen && win === main) {
          win.hide()
        } else {
          win.showInactive()
        }
      }

      assertReady()

      effectiveMode = 'desktop'
      status = 'active'
      health.startHeartbeat()
      options.getSpriteWindow()?.hide()
      publish()
    } catch (error) {
      await leave(errorMessage(error), false, activeDisplay !== null)
      throw error
    }
  }

  async function initialize(): Promise<void> {
    if (initialized || disposed) {
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

    if (!disposed) {
      health.start()
      await refreshDisplayResolutions()
      publish()
    }
  }

  function onDisplaysChanged(): void {
    void refreshDisplayResolutions()
    const activeDisplayId = activeDisplay?.id
    const displays = screen.getAllDisplays()
    const current = displays.find(display => display.id === activeDisplayId)

    if (activeDisplay && !current && ['starting', 'active', 'recovering'].includes(status)) {
      scheduleRecovery('桌面显示器已断开，已恢复窗口模式。再次启用将使用当前显示器。', false)

      return
    }

    if (activeDisplay && current && sameDisplayGeometry(activeDisplay, current)) {
      publish()

      return
    }

    if (status === 'starting') {
      scheduleRecovery('桌面准备期间显示器已变化，请重新选择桌面模式。')
    } else if (status === 'active') {
      const currentGeneration = startupGeneration
      health.deferDisplayChange(() => {
        void serial(async () => {
          if (status !== 'active' || currentGeneration !== startupGeneration) {
            return
          }

          await leave(undefined, true)

          if (currentGeneration === startupGeneration) {
            await enter(undefined, current?.id)
          }
        }).catch(error => options.log(errorMessage(error)))
      })
    } else {
      publish()
    }
  }

  function assertDesktop(sender: WebContents): void {
    if (disposed) {
      throw new Error('桌面呈现已关闭。')
    }

    if (!isDesktopSender(sender)) {
      throw new Error('仅桌面入口允许此操作。')
    }
  }

  function captureDesktopEligibility(sender: WebContents, epoch = stageEpoch): () => boolean {
    assertDesktop(sender)
    const generation = startupGeneration
    const identity = options.authIdentity()

    return () =>
      !disposed &&
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

  function captureApplicationEligibility(sender: WebContents, interaction = true): ApplicationEligibility {
    const eligible = captureDesktopEligibility(sender)

    if (!interaction) {
      return { current: eligible, continuation: eligible }
    }

    const currentInteraction = windows.captureInteraction(sender)
    const continuationInteraction = windows.captureInteraction(sender, false)

    return {
      current: () => eligible() && currentInteraction(),
      continuation: () => eligible() && continuationInteraction()
    }
  }

  function assertSettings(sender: WebContents): void {
    if (disposed) {
      throw new Error('桌面呈现已关闭。')
    }

    if (!isDesktopSender(sender) && !options.isSettingsSender(sender)) {
      throw new Error('仅本机设置入口允许此操作。')
    }
  }

  function assertModeInputAvailable(mode: PresentationMode): void {
    const blocked = uiState.blockedReason()

    if (mode !== effectiveMode && blocked) {
      throw new Error(blocked)
    }
  }

  function setMode(mode: PresentationMode, sender?: WebContents): Promise<PresentationState> {
    if (disposed) {
      return Promise.reject(new Error('桌面呈现已关闭。'))
    }

    if (sender) {
      assertSettings(sender)
    }

    assertModeInputAvailable(mode)

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

      if (mode === effectiveMode && preferences.get().mode === mode && (status === 'active' || status === 'inactive')) {
        return snapshot()
      }

      assertModeInputAvailable(mode)

      const source = effectiveMode === 'desktop' ? windows.getInteractive() : options.getMainWindow()
      const sourceVisible = source?.isVisible() ?? false
      const sourceMinimized = source?.isMinimized() ?? false
      const identity = options.authIdentity()
      const previousStatus = status
      status = mode === 'desktop' ? 'starting' : 'recovering'
      publish()

      try {
        if (source && !source.isDestroyed() && uiState.isOwner(source.webContents)) {
          await uiState.capture(source)
        }

        if (identity !== options.authIdentity()) {
          throw new Error('界面交接账户已变化。')
        }

        assertModeInputAvailable(mode)

        if (mode === 'window' && effectiveMode === 'desktop') {
          await options.restoreMain(uiState.getSnapshot()?.navigation ?? { page: 'chat' })
        }

        assertModeInputAvailable(mode)

        await preferences.set({ mode })
      } catch (error) {
        if (mode === 'window' && effectiveMode === 'desktop') {
          await options.closeSurfaces()
          options.getSpriteWindow()?.hide()

          if (identity === options.authIdentity() && source && !source.isDestroyed()) {
            try {
              await restoreUiState(source)
            } catch (restoreError) {
              await leave(`界面交接恢复失败：${errorMessage(restoreError)}`)
              throw error
            }
          }
        }

        status = previousStatus
        publish()
        throw error
      }

      if (mode === 'desktop' && currentGeneration !== startupGeneration) {
        return snapshot()
      }

      suppressAutoRestore = false
      prepareDesktopMedia = mode === 'desktop'
      publish()

      if (mode === 'desktop') {
        try {
          await enter(sender)

          if (effectiveMode === 'desktop' && snapshot().status === 'active' && existsSync(mainInterruptedFile)) {
            try {
              unlinkSync(mainInterruptedFile)
            } catch (error) {
              options.log(`[desktop] main interruption marker cleanup: ${errorMessage(error)}`)
            }
          }
        } catch (error) {
          if (snapshot().status !== 'failed') {
            status = 'failed'
            failureReason = errorMessage(error)
            publish()
          }

          if (
            identity === options.authIdentity() &&
            source &&
            !source.isDestroyed() &&
            (sourceVisible || sourceMinimized)
          ) {
            await options.restoreMain(uiState.getSnapshot()?.navigation ?? {})

            if (sourceMinimized) {
              source.minimize()
            }
          }

          throw error
        }
      } else {
        await leave()
        const target = options.getMainWindow()

        if (target && !target.isDestroyed() && target.isVisible()) {
          options.getSpriteWindow()?.hide()
          target.focus()
        }
      }

      return snapshot()
    })
  }

  function setDisplay(id: number, sender: WebContents): Promise<PresentationState> {
    assertSettings(sender)
    const currentGeneration = startupGeneration

    return serial(async () => {
      assertSettings(sender)
      const block = uiState.blockedReason()

      if (block) {
        throw new Error(block)
      }

      if (!screen.getAllDisplays().some(display => display.id === id)) {
        throw new Error('显示器不可用。')
      }

      if (preferences.get().displayId === id && (effectiveMode !== 'desktop' || activeDisplay?.id === id)) {
        return snapshot()
      }

      const interactive = windows.getInteractive()

      if (interactive && !interactive.isDestroyed()) {
        await uiState.capture(interactive)
      }

      await preferences.set({ displayId: id })

      if (effectiveMode === 'desktop' && activeDisplay?.id !== id && currentGeneration === startupGeneration) {
        await leave(undefined, true)

        if (currentGeneration === startupGeneration) {
          await enter()
        }
      }

      publish()

      return snapshot()
    })
  }

  function hostBecameReady(): void | Promise<void> {
    if (disposed) {
      return
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
  }

  function registerIpc(ipcMain: IpcMain): void {
    uiState.register(ipcMain)
    disposeIpc = registerDesktopPresentationIpc(
      ipcMain,
      {
        isDesktopSender,
        isSpriteSender: sender => isSenderWindow(sender, options.getSpriteWindow()),
        desktopWindow: sender => (isDesktopSender(sender) ? windows.getInteractive() : null),
        backgroundWindow: windows.getBackgroundWindow,
        assertSettings,
        authenticated: options.authenticated,
        authIdentity: options.authIdentity
      },
      {
        getState: snapshot,
        setMode,
        setDisplay,
        companionActivity: (source, state, voicePreparing) => {
          if (disposed) {
            return
          }

          if (activityBySource[source].state !== state || activityBySource[source].voicePreparing !== voicePreparing) {
            activityBySource[source] = { state, voicePreparing }
            publish()
          }
        },
        reportReady: () => {
          const main = windows.getInteractive()

          if (main) {
            windows.reportReady(main)
          }

          health.heartbeat()
        },
        focus: (sender, epoch) => {
          const eligible = captureDesktopEligibility(sender, epoch)

          return serial(async () => {
            assertDesktop(sender)

            if (!eligible()) {
              return false
            }

            return native.focus(windows.getInteractive()!.getNativeWindowHandle(), eligible)
          })
        },
        heartbeat: health.heartbeat,
        hostReady: hostBecameReady,
        setBackground: (sender, raw) => (disposed ? Promise.resolve() : background.set(sender, raw)),
        backgroundReady: win => {
          sendToWindow(win, IPC.event.backgroundMedia, background.getState())
          windows.reportReady(win)
        },
        acknowledgeBackground: background.acknowledge,
        stageActivity: activity => {
          if (disposed) {
            return
          }

          lastActivity = activity
          background.publish()
        }
      }
    )
  }

  function stop(): Promise<void> {
    invalidateStartup('桌面准备已取消。')

    return serial(() => leave())
  }

  function dispose(): Promise<void> {
    if (!disposal) {
      disposed = true
      health.dispose()
      uiState.beginShutdown()
      applicationListeners.clear()
      // 精灵宿主会持续上报到窗口关闭；will-quit 才表示所有发送窗口已经退出。
      app.once('will-quit', () => {
        disposeIpc?.()
        disposeIpc = null
        uiState.dispose()
      })
      disposal = stop()
    }

    return disposal
  }

  async function restoreUiState(win: BrowserWindow, target?: AppNavigationTarget): Promise<void> {
    if (!uiState.isOwner(win.webContents)) {
      await uiState.restore(win, target)
    }
  }

  return {
    captureApplicationEligibility,
    getRunningApplications: () => runningApplications,
    onRunningApplicationsChanged: (listener: (state: RunningApplicationsState) => void): (() => void) => {
      applicationListeners.add(listener)

      return () => applicationListeners.delete(listener)
    },
    refreshApplications: (sender: WebContents): Promise<void> => {
      const eligible = captureDesktopEligibility(sender)

      return serial(() => native.refreshApplications(eligible))
    },
    activateExternal: (
      sender: WebContents,
      windowId: string,
      allowRemote: boolean,
      eligible: ApplicationEligibility
    ): Promise<void> => {
      assertDesktop(sender)

      return serial(async () => {
        const win = windows.getInteractive()

        if (!eligible.current() || fullscreen || !win || !win.isFocused()) {
          throw new Error('桌面已改变，请重新选择程序。')
        }

        // 桌面已在前台，沿用原始交互；不重新激活已失焦的桌面。
        win.focus()
        await native.activateExternal(windowId, allowRemote, eligible.current, eligible.continuation)
      })
    },
    launchExternal: (
      sender: WebContents,
      target: string,
      appId: string,
      eligible: ApplicationEligibility
    ): Promise<void> => {
      assertDesktop(sender)

      return serial(() => native.launchExternal(target, appId, eligible.current))
    },
    closeExternal: (sender: WebContents, windowIds: string[], eligible: ApplicationEligibility): Promise<void> => {
      assertDesktop(sender)

      return serial(() => native.closeExternal(windowIds, eligible.current))
    },
    getJumpListTasks: (sender: WebContents, target: string) => {
      const eligible = captureDesktopEligibility(sender)

      return native.getJumpListTasks(target, eligible)
    },
    activateJumpListTask: (
      sender: WebContents,
      target: string,
      taskId: string,
      eligible: ApplicationEligibility
    ): Promise<void> => {
      assertDesktop(sender)

      return serial(() => native.activateJumpListTask(target, taskId, eligible.current))
    },
    getState: snapshot,
    captureUiState: async (): Promise<void> => {
      const source = effectiveMode === 'desktop' ? windows.getInteractive() : options.getMainWindow()

      if (source && !source.isDestroyed() && uiState.isOwner(source.webContents)) {
        await uiState.capture(source)
      }
    },
    restoreUiState,
    setMode,
    getWindow: windows.getInteractive,
    isDesktopSender,
    registerIpc,
    initialize,
    stop,
    dispose,
    clearUiState: (removedAccountId: string) => uiState.forgetAccount(removedAccountId),
    accountChanged: () => {
      uiState.accountChanged()
      suppressAutoRestore = true
      prepareDesktopMedia = false
      background.accountChanged()
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
      const interactive = windows.getInteractive()

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
