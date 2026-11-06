import fs from 'node:fs'
import path from 'node:path'

import {
  app,
  type BrowserWindow,
  clipboard,
  dialog,
  net as electronNet,
  ipcMain,
  Menu,
  nativeImage,
  powerMonitor,
  safeStorage,
  session,
  Tray
} from 'electron'

import { IPC, type SpiritAgentUiTheme } from '@ipc/contracts'

import { createBackendClient, isRetryableBackendError } from '../backend/client'
import { createEnsureBackend } from '../backend/ensure-backend'
import { createDesktopGateway } from '../backend/gateway'
import { createBackendHttp, createElectronFetch } from '../backend/http'
import { createBackendSession } from '../backend/session'
import { createSessionRuntime } from '../backend/session-runtime'
import { createAssetDiskCache } from '../ipc/asset-disk-cache'
import { createAuthBroadcaster, type registerAuthIpc } from '../ipc/auth'
import { createBackendAssetReader } from '../ipc/connection'
import { registerMediaIpc } from '../ipc/media'
import { createRunnerHost } from '../ipc/runner'
import { createSessionHistoryDiskCache } from '../ipc/session-history-disk-cache'
import { cleanupShortcuts, syncShortcutsFromConfig } from '../ipc/shortcuts'
import { createVoicePlaybackStore } from '../ipc/voice-playback-store'
import { installAppQuit } from '../lifecycle/app-quit'
import { createAudioOutput } from '../lifecycle/audio-output'
import { createAutoUpdater } from '../lifecycle/auto-updater'
import { syncBundledSkills } from '../lifecycle/bundled-skills'
import { createDesktopLogger } from '../lifecycle/desktop-log'
import { createDesktopPresentation, type DesktopPresentation } from '../lifecycle/desktop-presentation'
import { applyAppIdentity, createMenu } from '../lifecycle/menu'
import { createOpenExternalUrl } from '../lifecycle/open-external-url'
import { applyChromiumSwitches } from '../lifecycle/platform'
import { createAppIconResolver, createRendererPaths } from '../lifecycle/renderer-paths'
import { acquireSingleInstance } from '../lifecycle/single-instance'
import { createSpriteWindowFactory } from '../lifecycle/sprite-window'
import { createSurfaceCompanionPreferences } from '../lifecycle/surface-companion'
import { createSurfaceWindowFactory } from '../lifecycle/surface-window'
import { createSurfacesManager, type SurfacesManager } from '../lifecycle/surfaces'
import {
  destroyTray,
  installCloseInterceptor,
  installTray,
  rebuildTrayMenu,
  registerSingleInstanceForwarder,
  showMainWindow
} from '../lifecycle/tray'
import { createContextMenuHelpers } from '../lifecycle/window-context-menu-helpers'
import { createWindowHandlers } from '../lifecycle/window-handlers'
import { createZoomPersistence } from '../lifecycle/zoom-persistence'
import { createRunnerBridge } from '../runner/bridge'
import { createRunnerProcess } from '../runner/process'
import { createRunnerWsServer } from '../runner/rpc-ws'
import { RunnerUpdater } from '../runner/updater'
import { ASSET_FETCH_TIMEOUT_MS, DEFAULT_CSP_POLICY, DEV_CSP_POLICY } from '../security/hardening'
import { isSenderWindow } from '../security/ipc-trust'
import { resolveDesktopHome } from '../security/paths'
import type { BackendSessionPort } from '../shared/backend-port'
import { createConfigSync, uiThemeFromConfig } from '../shared/lib/config-sync'
import * as runnerConfigStore from '../shared/lib/runner-config-store'
import { broadcastToAllWindows, fileExists } from '../shared/utils'

export interface EntryServices {
  appRoot: string
  appQuit: ReturnType<typeof installAppQuit>
  assetDiskCache: ReturnType<typeof createAssetDiskCache>
  audioOutput: ReturnType<typeof createAudioOutput>
  authBroadcaster: ReturnType<typeof createAuthBroadcaster>
  autoUpdater: ReturnType<typeof createAutoUpdater>
  backendHttp: ReturnType<typeof createBackendHttp>
  configSync: ReturnType<typeof createConfigSync>
  ensureBackend: ReturnType<typeof createEnsureBackend>['ensureBackend']
  ensureBackendSession: () => BackendSessionPort
  getAppIconPath: ReturnType<typeof createAppIconResolver>
  getMainWindow: () => BrowserWindow | null
  gatewayRuntime: ReturnType<typeof createDesktopGateway> | undefined
  isPackaged: boolean
  mediaIpc: ReturnType<typeof registerMediaIpc>
  presentation: DesktopPresentation | null
  publishUiTheme: (theme: SpiritAgentUiTheme) => void
  readBackendAsset: ReturnType<typeof createBackendAssetReader>
  resetBackendCache: () => void
  rememberLog: (message: string) => void
  runnerHost: ReturnType<typeof createRunnerHost>
  sessionHistoryDiskCache: ReturnType<typeof createSessionHistoryDiskCache>
  sessionRuntime: ReturnType<typeof createSessionRuntime>
  showMainWindow: () => void
  spiritagentHome: string
  surfaces: SurfacesManager | null
  voicePlaybackStore: ReturnType<typeof createVoicePlaybackStore>
  start: (authActions: ReturnType<typeof registerAuthIpc>) => Promise<void>
}

/** 组合根：只按依赖顺序构造服务，不注册 IPC，也不安装窗口。 */
export function createEntryServices(): EntryServices {
  const devServer = process.env.SPIRITAGENT_DESKTOP_DEV_SERVER
  const isPackaged = app.isPackaged
  const isMac = process.platform === 'darwin'
  const appRoot = app.getAppPath()

  const singleInstance = acquireSingleInstance(app)

  let mainWindow: BrowserWindow | null = null
  const getMainWindow = (): BrowserWindow | null => mainWindow
  let surfaces: null | SurfacesManager = null
  let presentation: DesktopPresentation | null = null

  const publishUiTheme = (theme: SpiritAgentUiTheme): void => {
    presentation?.setUiTheme(theme)
    broadcastToAllWindows(IPC.event.uiThemeChanged, { theme })
  }

  const remoteDisplayReason = applyChromiumSwitches(app)

  const spiritagentHome = resolveDesktopHome()
  fs.mkdirSync(spiritagentHome, { recursive: true })
  app.setPath('userData', spiritagentHome)

  const desktopLogger = createDesktopLogger({ isPackaged, spiritagentHome })

  const { rememberLog } = desktopLogger
  const audioOutput = createAudioOutput(rememberLog)

  // 须先于会话恢复与 Runner 自动启动，使 Runner 与技能索引读到当前版本的随包技能。
  syncBundledSkills({
    app,
    log: rememberLog,
    resourcesPath: process.resourcesPath,
    spiritagentHome
  })

  runnerConfigStore.init({ spiritagentHome })

  const appName = '唤生'
  const electronFetch = createElectronFetch(electronNet)
  const backendHttp = createBackendHttp(electronFetch)

  let sessionRuntime: ReturnType<typeof createSessionRuntime>

  const ensureBackendSession = (): BackendSessionPort => sessionRuntime.ensureBackendSession()

  const { ensureBackend, resetBackendCache } = createEnsureBackend({
    appName,
    backendHttp,
    spiritagentHome,
    logBootStep: message => rememberLog(`[boot] ${message}`),
    getAuthToken: () => ensureBackendSession().getToken(),
    getCurrentBaseUrl: () => ensureBackendSession().getSession()?.baseUrl ?? null
  })

  const configSync = createConfigSync({
    createBackendClient: ({ baseUrl }) => createBackendClient({ baseUrl, fetch: electronFetch }),
    ensureBackend: () => ensureBackend(),
    isRetryableError: isRetryableBackendError,
    log: rememberLog,
    onHydrated: payload => {
      const theme = uiThemeFromConfig(runnerConfigStore.read())

      syncShortcutsFromConfig()

      broadcastToAllWindows(IPC.event.prefsHydrated, payload)

      if (theme) {
        publishUiTheme(theme)
      }

      rebuildTrayMenu()
    }
  })

  runnerConfigStore.setCloudSync(configSync)

  const seedUiTheme = (): string | undefined => uiThemeFromConfig(runnerConfigStore.read())

  const { rendererUrlFor } = createRendererPaths({
    appRoot,
    devServer,
    isPackaged,
    rememberLog
  })

  const getAppIconPath = createAppIconResolver(appRoot)

  applyAppIdentity(app, appName)

  const zoomPersistence = createZoomPersistence({ app, rememberLog })
  const surfaceCompanionPreferences = createSurfaceCompanionPreferences(app)
  const contextMenuHelpers = createContextMenuHelpers({ electronNet })
  const openExternalUrl = createOpenExternalUrl(rememberLog)

  const menu = createMenu({
    app,
    appName,
    getMainWindow,
    isMac,
    menu: Menu,
    minimizeWindow: win => surfaces?.minimizeWindow(win),
    toggleMaximizeWindow: win => surfaces?.toggleMaximizeWindow(win),
    zoomPersistence
  })

  const windowHandlers = createWindowHandlers({
    clipboard,
    contextMenuHelpers,
    cspPolicies: { dev: DEV_CSP_POLICY, prod: DEFAULT_CSP_POLICY },
    isDevServer: devServer,
    isMac,
    isPackaged,
    menu: Menu,
    openExternalUrl,
    rememberLog,
    session,
    zoomPersistence
  })

  const preloadPath = path.join(import.meta.dirname, 'preload.cjs')

  const { createSpriteWindow, syncSpriteToDisplay } = createSpriteWindowFactory({
    app,
    getAppIconPath,
    getMainWindow,
    installCloseInterceptor,
    isMac,
    preloadPath,
    rememberLog,
    rendererUrlFor,
    seedTheme: seedUiTheme,
    setMainWindow: win => {
      mainWindow = win
    },
    spriteTransparent: !remoteDisplayReason,
    windowHandlers,
    zoomPersistence
  })

  const { createSurfaceWindow, navigateSurfaceWindow } = createSurfaceWindowFactory({
    app,
    appName,
    getAppIconPath,
    getSurfaces: () => surfaces,
    isMac,
    isQuitting: () => appQuit.isQuitting(),
    preloadPath,
    rebuildTrayMenu,
    rememberLog,
    rendererUrlFor,
    seedTheme: seedUiTheme,
    windowHandlers,
    zoomPersistence
  })

  let runnerHost: ReturnType<typeof createRunnerHost>
  let gatewayRuntime: ReturnType<typeof createDesktopGateway> | undefined

  const authBroadcaster = createAuthBroadcaster({
    autoStartBridge: () => runnerHost.autoStart(),
    onGatewayAuthChanged: () => gatewayRuntime?.authChanged(),
    configSync,
    ensureBackendSession,
    log: rememberLog,
    rebuildTrayMenu
  })

  surfaces = createSurfacesManager({
    authIdentity: () => ensureBackendSession().getSession()?.sessionId ?? null,
    createWindow: createSurfaceWindow,
    getCompanionPreference: () => surfaceCompanionPreferences.get(),
    getSpriteWindow: getMainWindow,
    isQuitting: () => appQuit.isQuitting(),
    navigateWindow: navigateSurfaceWindow,
    prepareWindow: (win, target) => presentation?.restoreUiState(win, target) ?? Promise.resolve(),
    isPreferencesSender: sender => presentation?.isDesktopSender(sender) ?? false,
    rememberLog,
    saveCompanionPreference: preference => surfaceCompanionPreferences.set(preference),
    syncSpriteToDisplay,
    routeToDesktop: payload => {
      const state = presentation?.getState()

      if (state && ['starting', 'recovering'].includes(state.status)) {
        throw new Error('正在切换运行模式，请稍后打开主窗口。')
      }

      return presentation?.navigate(payload) ?? false
    }
  })
  surfaces.registerIpcHandlers({ ipcMain })

  presentation = createDesktopPresentation({
    userData: spiritagentHome,
    preloadPath,
    backgroundPreloadPath: path.join(import.meta.dirname, 'preload-background.cjs'),
    helperPath: isPackaged
      ? path.join(process.resourcesPath, 'desktop-host.exe')
      : path.join(appRoot, 'build', process.arch, 'desktop-host.exe'),
    developmentPreparationError: isPackaged ? undefined : process.env.SPIRITAGENT_DESKTOP_DEV_PREPARATION_ERROR,
    rendererUrlFor,
    seedTheme: seedUiTheme,
    getSpriteWindow: getMainWindow,
    getMainWindow: () => surfaces?.getWindow() ?? null,
    restoreMain: target => surfaces?.restoreMainWindow(target) ?? Promise.resolve(),
    isSettingsSender: sender => Boolean(isSenderWindow(sender, getMainWindow()) || surfaces?.isSurfaceSender(sender)),
    closeSurfaces: () => surfaces?.closeSurface() ?? Promise.resolve(),
    restoreSprite: () => {
      if (!appQuit.isQuitting()) {
        zoomPersistence.restorePersistedZoomLevel(getMainWindow())
        showMainWindow()
      }
    },
    authenticated: () => Boolean(sessionRuntime.ensureBackendSession().getSession()?.hasToken),
    authIdentity: () => sessionRuntime.ensureBackendSession().getSession()?.sessionId ?? null,
    authAccountId: () => sessionRuntime.ensureBackendSession().getSession()?.accountId ?? null,
    loadMedia: async (sender, reference) => {
      const asset = await readBackendAsset(sender, { ...reference, preferCache: true })

      return { bytes: new Uint8Array(asset.buffer), mime: asset.mime }
    },
    lockZoom: zoomPersistence.lockZoom,
    installWindowHandlers: windowHandlers.installSurfaceWindowHandlers,
    log: rememberLog,
    onModeChanged: rebuildTrayMenu
  })
  presentation.registerIpc(ipcMain)

  sessionRuntime = createSessionRuntime({
    createSession: createBackendSession,
    desktopVersion: () => app.getVersion(),
    fetchImpl: electronFetch,
    log: rememberLog,
    onRestored: snapshot => authBroadcaster.onSessionRestored(snapshot),
    safeStorage,
    spiritagentHome,
    userDataDir: app.getPath('userData')
  })

  const assetDiskCache = createAssetDiskCache({ spiritagentHome })

  const readBackendAsset = createBackendAssetReader({
    assetDiskCache,
    assetFetchTimeoutMs: ASSET_FETCH_TIMEOUT_MS,
    ensureBackend,
    fetchImpl: electronFetch,
    getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
    getSelectedAccountId: () => sessionRuntime.ensureBackendSession().getSelectedAccountId()
  })

  const sessionHistoryDiskCache = createSessionHistoryDiskCache({ spiritagentHome })
  const voicePlaybackStore = createVoicePlaybackStore({ spiritagentHome })

  const mediaIpc = registerMediaIpc({
    spiritagentHome,
    prepareAudioOutput: audioOutput.prepare,
    ensureBackend,
    invokeApi: backendHttp.invoke,
    getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
    getSelectedAccountId: () => sessionRuntime.ensureBackendSession().getSelectedAccountId(),
    ipcMain,
    log: rememberLog
  })

  runnerHost = createRunnerHost({
    createRunnerBridge,
    createRunnerProcess,
    createRunnerWsServer,
    ensureBackendSession,
    fileExists,
    getMainWindow,
    rememberLog,
    spiritagentHome
  })

  gatewayRuntime = createDesktopGateway({
    ensureBackend,
    getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
    getTools: () => runnerHost.getBridge()?.getTools() ?? [],
    dispatchCall: request => runnerHost.dispatchCall(request),
    queryCall: callId => {
      const bridge = runnerHost.getBridge()

      if (!bridge) {
        return Promise.reject(new Error('Runner is not available for result recovery'))
      }

      return bridge.dispatch('calls.get', { call_id: callId })
    },
    cancelCall: callId => runnerHost.cancelCall(callId),
    mintWsTicket: backendHttp.mintWsTicket,
    syncTimezone: async (baseUrl, token) => {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

      if (timezone) {
        await backendHttp.invoke(baseUrl, token, 'config.put_config', { body: { config: { timezone } } })
      }
    },
    onState: (snapshot, authSessionId) =>
      broadcastToAllWindows(IPC.event.gatewayStateChanged, { ...snapshot, authSessionId }),
    onEvent: (event, authSessionId) => broadcastToAllWindows(IPC.event.gatewayEvent, { event, authSessionId }),
    onExpired: authSessionId => broadcastToAllWindows(IPC.event.authSessionExpired, authSessionId),
    log: rememberLog
  })
  runnerHost.onStatus(event =>
    gatewayRuntime?.runnerStatus(event.type === 'running' || event.type === 'runner_ready' ? (event.tools ?? []) : [])
  )
  powerMonitor.on('resume', () => gatewayRuntime?.reconnect())
  app.on('before-quit', () => gatewayRuntime?.close())

  const autoUpdater = createAutoUpdater({
    app,
    runtime: {
      getRunnerBridge: () => runnerHost.getBridge(),
      spiritagentHome
    },
    createRunnerUpdater: deps => new RunnerUpdater(deps),
    fetchImpl: electronFetch,
    spiritagentHome
  })

  const appQuit = installAppQuit({
    app,
    cleanupShortcuts,
    destroyTray,
    flushConfig: () => configSync.flush(),
    flushLog: () => desktopLogger.flushSync(),
    flushPlayback: () => voicePlaybackStore.flush(),
    stopAudioOutput: audioOutput.dispose,
    log: rememberLog,
    restoreDesktop: () => presentation?.dispose() ?? Promise.resolve(),
    stopRunner: () => runnerHost.getBridge()?.stop({ reason: 'app-quit' }) ?? Promise.resolve()
  })

  const start = async (authActions: ReturnType<typeof registerAuthIpc>): Promise<void> => {
    await app.whenReady().then(async () => {
      if (appQuit.isQuitting()) {
        return
      }

      void audioOutput.prepare().catch(error => rememberLog(`[audio-output] Startup failed: ${String(error)}`))

      setTimeout(() => {
        if (!appQuit.isQuitting()) {
          authBroadcaster.autoStartBridgeIfSignedIn()
        }
      }, 200).unref()

      await presentation?.initialize()
      surfaces?.watchSystemEvents()
      menu.installApplicationMenu()
      windowHandlers.installMediaPermissions()
      windowHandlers.installContentSecurityPolicy()
      windowHandlers.configureSpellChecker(app)
      syncShortcutsFromConfig()
      autoUpdater.setup()

      await autoUpdater.installPendingRunnerUpdate()

      if (appQuit.isQuitting()) {
        return
      }

      createSpriteWindow()

      const trayDeps = {
        app,
        createWindow: createSpriteWindow,
        dialog,
        ensureBackendSession,
        getAppIconPath,
        getMainWindow,
        Menu,
        nativeImage,
        presentation: presentation ?? undefined,
        rememberLog,
        removeAccount: authActions.removeAccount,
        switchAccount: authActions.switchAccount,
        Tray,
        getIsQuitting: () => appQuit.isQuitting(),
        surfaces: surfaces ?? undefined
      }

      installTray(trayDeps)
      registerSingleInstanceForwarder(trayDeps)
      // 早期第二实例事件须在 installTray 写入托盘依赖后兑现：showMainWindow 依赖它。
      singleInstance.replayEarlySecondInstance(showMainWindow)

      if (!isPackaged && process.connected) {
        process.send?.({ event: 'spiritagent:dev-ready' }, error => {
          if (error) {
            rememberLog(`[dev] launcher readiness failed: ${error.message}`)
          }
        })
      }

      app.on('activate', () => showMainWindow())
    })
  }

  if (!isPackaged && process.connected) {
    process.on('message', (message: unknown) => {
      if (
        !message ||
        typeof message !== 'object' ||
        !('command' in message) ||
        message.command !== 'spiritagent:dev-restart' ||
        Object.keys(message).length !== 1
      ) {
        return
      }

      rememberLog('[dev] graceful restart requested')
      app.quit()
    })
    process.once('disconnect', () => {
      rememberLog('[dev] launcher disconnected, requesting normal exit')
      app.quit()
    })
  }

  return {
    appRoot,
    appQuit,
    assetDiskCache,
    audioOutput,
    authBroadcaster,
    autoUpdater,
    backendHttp,
    configSync,
    ensureBackend,
    ensureBackendSession,
    getAppIconPath,
    getMainWindow,
    get gatewayRuntime() {
      return gatewayRuntime
    },
    isPackaged,
    mediaIpc,
    get presentation() {
      return presentation
    },
    publishUiTheme,
    readBackendAsset,
    rememberLog,
    resetBackendCache,
    get runnerHost() {
      return runnerHost
    },
    sessionHistoryDiskCache,
    sessionRuntime,
    showMainWindow,
    spiritagentHome,
    get surfaces() {
      return surfaces
    },
    voicePlaybackStore,
    start
  }
}
