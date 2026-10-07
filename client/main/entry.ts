import fs from 'node:fs'
import path from 'node:path'

import { IPC } from '@ipc/contracts'
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
  screen,
  session,
  Tray
} from 'electron'

import { createBackendClient, isRetryableBackendError } from './backend/client'
import { createEnsureBackend } from './backend/ensure-backend'
import { createBackendHttp, createElectronFetch } from './backend/http'
import { createBackendSession } from './backend/session'
import { createSessionRuntime } from './backend/session-runtime'
import { createAssetDiskCache } from './ipc/asset-disk-cache'
import { createAuthBroadcaster, registerAuthIpc, registerDesktopAccountIpc } from './ipc/auth'
import { registerClipboardIpc } from './ipc/clipboard'
import { registerConnectionIpc } from './ipc/connection'
import { registerDesktopDock } from './ipc/desktop-dock'
import { registerFilesIpc } from './ipc/files'
import { registerGatewayIpc } from './ipc/gateway'
import { registerLogIpc } from './ipc/log'
import { registerMediaIpc } from './ipc/media'
import { registerOnboardingAudioIpc } from './ipc/onboarding-audio'
import { registerPrefsIpc } from './ipc/prefs'
import { createRunnerHost } from './ipc/runner'
import { registerRunnerConfigIpc } from './ipc/runner-config'
import { registerSessionHistoryIpc } from './ipc/session-history'
import { createSessionHistoryDiskCache } from './ipc/session-history-disk-cache'
import { cleanupShortcuts, registerShortcutsIpc, syncShortcutsFromConfig } from './ipc/shortcuts'
import { registerSkillsIpc } from './ipc/skills'
import { registerSpriteIpc } from './ipc/sprite'
import { registerSystemIpc } from './ipc/system'
import { registerUiThemeIpc } from './ipc/ui-theme'
import { registerUpdateIpc } from './ipc/update'
import { registerVoicePlaybackIpc } from './ipc/voice-playback'
import { createVoicePlaybackStore } from './ipc/voice-playback-store'
import { installAppQuit } from './lifecycle/app-quit'
import { createAutoUpdater } from './lifecycle/auto-updater'
import { syncBundledSkills } from './lifecycle/bundled-skills'
import { createDesktopLogger } from './lifecycle/desktop-log'
import { createDesktopPresentation, type DesktopPresentation } from './lifecycle/desktop-presentation'
import { applyAppIdentity, createMenu } from './lifecycle/menu'
import { createOpenExternalUrl } from './lifecycle/open-external-url'
import { applyChromiumSwitches } from './lifecycle/platform'
import { createAppIconResolver, createRendererPaths } from './lifecycle/renderer-paths'
import { acquireSingleInstance } from './lifecycle/single-instance'
import { createSpriteWindowFactory } from './lifecycle/sprite-window'
import { createSurfaceCompanionPreferences } from './lifecycle/surface-companion'
import { createSurfaceWindowFactory } from './lifecycle/surface-window'
import { createSurfacesManager, type SurfacesManager } from './lifecycle/surfaces'
import {
  destroyTray,
  installCloseInterceptor,
  installTray,
  rebuildTrayMenu,
  registerSingleInstanceForwarder,
  showMainWindow,
  toggleMainWindow
} from './lifecycle/tray'
import { createContextMenuHelpers } from './lifecycle/window-context-menu-helpers'
import { createWindowHandlers } from './lifecycle/window-handlers'
import { createZoomPersistence } from './lifecycle/zoom-persistence'
import { createRunnerBridge } from './runner/bridge'
import { createRunnerProcess } from './runner/process'
import { createReverseRpc } from './runner/reverse-rpc'
import { createRunnerWsServer } from './runner/rpc-ws'
import { RunnerUpdater } from './runner/updater'
import {
  DEFAULT_CSP_POLICY,
  DEFAULT_FETCH_TIMEOUT_MS,
  DEV_CSP_POLICY,
  resolvePathTimeoutMs
} from './security/hardening'
import { isSenderWindow } from './security/ipc-trust'
import { resolveDesktopHome } from './security/paths'
import type { BackendSessionPort } from './shared/backend-port'
import { readStoredBackendUrl } from './shared/config'
import { createConfigSync, uiThemeFromConfig } from './shared/lib/config-sync'
import * as runnerConfigStore from './shared/lib/runner-config-store'
import { broadcastToAllWindows, fileExists, sendToWindow } from './shared/utils'

const DEV_SERVER = process.env.SPIRITAGENT_DESKTOP_DEV_SERVER
const IS_PACKAGED = app.isPackaged
const IS_MAC = process.platform === 'darwin'
const APP_ROOT = app.getAppPath()

const singleInstance = acquireSingleInstance(app)

let mainWindow: BrowserWindow | null = null
const getMainWindow = (): BrowserWindow | null => mainWindow
let surfaces: null | SurfacesManager = null
let presentation: DesktopPresentation | null = null
// sessionRuntime 在下方创建；各端口晚绑定读取。
const ensureBackendSession = (): BackendSessionPort => sessionRuntime.ensureBackendSession()

const REMOTE_DISPLAY_REASON = applyChromiumSwitches(app)

const SPIRITAGENT_HOME = resolveDesktopHome()
fs.mkdirSync(SPIRITAGENT_HOME, { recursive: true })
app.setPath('userData', SPIRITAGENT_HOME)

const desktopLogger = createDesktopLogger({
  isPackaged: IS_PACKAGED,
  spiritagentHome: SPIRITAGENT_HOME
})

const { rememberLog } = desktopLogger

// 须先于会话恢复与 Runner 自动启动，使 Runner 与技能索引读到当前版本的随包技能。
syncBundledSkills({
  app,
  log: rememberLog,
  resourcesPath: process.resourcesPath,
  spiritagentHome: SPIRITAGENT_HOME
})

runnerConfigStore.init({ spiritagentHome: SPIRITAGENT_HOME })

const APP_NAME = '唤生'

const electronFetch = createElectronFetch(electronNet)

const backendHttp = createBackendHttp({
  electronNet,
  spiritagentHome: SPIRITAGENT_HOME
})

const { ensureBackend, resetBackendCache } = createEnsureBackend({
  appName: APP_NAME,
  backendHttp,
  logBootStep: message => rememberLog(`[boot] ${message}`),
  getAuthToken: () => sessionRuntime.ensureBackendSession().getToken(),
  getCurrentBaseUrl: () => ensureBackendSession().getSession()?.baseUrl ?? null
})

// 云端配置同步协调器：backend user_settings 为真源，desktop-settings.json 是镜像（机密与设备相关节仅本机，见 shared/lib/config-sync.ts）。
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
      broadcastToAllWindows(IPC.event.uiThemeChanged, { theme })
    }

    rebuildTrayMenu()
  }
})

runnerConfigStore.setCloudSync(configSync)

const seedUiTheme = (): string | undefined => uiThemeFromConfig(runnerConfigStore.read())

const { rendererUrlFor } = createRendererPaths({
  appRoot: APP_ROOT,
  devServer: DEV_SERVER,
  isPackaged: IS_PACKAGED,
  rememberLog
})

const getAppIconPath = createAppIconResolver(APP_ROOT)

applyAppIdentity(app, APP_NAME)

const zoomPersistence = createZoomPersistence({ app, rememberLog })
const surfaceCompanionPreferences = createSurfaceCompanionPreferences(app)

const contextMenuHelpers = createContextMenuHelpers({ electronNet })

const openExternalUrl = createOpenExternalUrl(rememberLog)

const menu = createMenu({
  app,
  appName: APP_NAME,
  getMainWindow,
  isMac: IS_MAC,
  menu: Menu,
  minimizeWindow: win => surfaces?.minimizeWindow(win),
  toggleMaximizeWindow: win => surfaces?.toggleMaximizeWindow(win),
  zoomPersistence
})

const windowHandlers = createWindowHandlers({
  clipboard,
  contextMenuHelpers,
  cspPolicies: { dev: DEV_CSP_POLICY, prod: DEFAULT_CSP_POLICY },
  isDevServer: DEV_SERVER,
  isMac: IS_MAC,
  isPackaged: IS_PACKAGED,
  menu: Menu,
  openExternalUrl,
  powerMonitor,
  rememberLog,
  sendPowerResume: () => sendToWindow(mainWindow, IPC.event.powerResume),
  session,
  zoomPersistence
})

const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON
const PRELOAD_PATH = path.join(import.meta.dirname, 'preload.cjs')

const { createSpriteWindow, syncSpriteToDisplay } = createSpriteWindowFactory({
  app,
  getAppIconPath,
  getMainWindow,
  installCloseInterceptor,
  isMac: IS_MAC,
  preloadPath: PRELOAD_PATH,
  rememberLog,
  rendererUrlFor,
  seedTheme: seedUiTheme,
  setMainWindow: win => {
    mainWindow = win
  },
  spriteTransparent: SPRITE_TRANSPARENT,
  windowHandlers,
  zoomPersistence
})

const { createSurfaceWindow, navigateSurfaceWindow } = createSurfaceWindowFactory({
  app,
  appName: APP_NAME,
  getAppIconPath,
  getCompanionPreference: id => surfaceCompanionPreferences.get(id),
  getSurfaces: () => surfaces,
  isMac: IS_MAC,
  preloadPath: PRELOAD_PATH,
  rebuildTrayMenu,
  rendererUrlFor,
  seedTheme: seedUiTheme,
  windowHandlers,
  zoomPersistence
})

const authBroadcaster = createAuthBroadcaster({
  autoStartBridge: () => runnerHost.autoStart(),
  configSync,
  ensureBackendSession,
  log: rememberLog,
  rebuildTrayMenu,
  resetPlaybackClaims: () => surfaces?.resetPlaybackClaims()
})

registerSystemIpc({
  electron: { app },
  ipcMain
})
registerUiThemeIpc({ ipcMain, log: rememberLog })
registerPrefsIpc({ ipcMain, log: rememberLog })

surfaces = createSurfacesManager({
  createWindow: createSurfaceWindow,
  getCompanionPreference: id => surfaceCompanionPreferences.get(id),
  getSpriteWindow: getMainWindow,
  navigateWindow: navigateSurfaceWindow,
  rememberLog,
  saveCompanionPreference: (id, preference) => surfaceCompanionPreferences.set(id, preference),
  syncSpriteToDisplay,
  routeToDesktop: payload => presentation?.navigate(payload) ?? false
})
surfaces.registerIpcHandlers({ ipcMain })
surfaces.hydrateLastSurface()
presentation = createDesktopPresentation({
  userData: SPIRITAGENT_HOME,
  preloadPath: PRELOAD_PATH,
  backgroundPreloadPath: path.join(import.meta.dirname, 'preload-background.cjs'),
  helperPath: IS_PACKAGED
    ? path.join(process.resourcesPath, 'desktop-host.exe')
    : path.join(APP_ROOT, 'build', process.arch, 'desktop-host.exe'),
  developmentPreparationError: IS_PACKAGED ? undefined : process.env.SPIRITAGENT_DESKTOP_DEV_PREPARATION_ERROR,
  rendererUrlFor,
  seedTheme: seedUiTheme,
  getSpriteWindow: getMainWindow,
  isSettingsSender: sender =>
    Boolean(
      isSenderWindow(sender, getMainWindow()) ||
      surfaces?.isSurfaceSender('living', sender) ||
      surfaces?.isSurfaceSender('workbench', sender)
    ),
  closeSurfaces: () => surfaces?.closeSurface() ?? Promise.resolve(),
  restoreSprite: () => {
    if (!appQuit.isQuitting()) {
      zoomPersistence.restorePersistedZoomLevel(getMainWindow())
      showMainWindow()
    }
  },
  authenticated: () => Boolean(sessionRuntime.ensureBackendSession().getSession()?.hasToken),
  authIdentity: () => sessionRuntime.ensureBackendSession().getSession()?.sessionId ?? null,
  lockZoom: zoomPersistence.lockZoom,
  installWindowHandlers: windowHandlers.installSurfaceWindowHandlers,
  log: rememberLog,
  onModeChanged: rebuildTrayMenu
})
presentation.registerIpc(ipcMain)
const desktopPresentation = presentation
registerDesktopDock({
  ipcMain,
  userData: SPIRITAGENT_HOME,
  isDesktopSender: sender => presentation?.isDesktopSender(sender) ?? false,
  getDesktopWindow: desktopPresentation.getWindow,
  getRunningApplications: desktopPresentation.getRunningApplications,
  onRunningApplicationsChanged: desktopPresentation.onRunningApplicationsChanged,
  captureEligibility: desktopPresentation.captureApplicationEligibility,
  refreshApplications: desktopPresentation.refreshApplications,
  activateExternal: desktopPresentation.activateExternal,
  closeExternal: desktopPresentation.closeExternal
})

registerShortcutsIpc({ ipcMain, rememberLog, surfaces, toggleMainWindow })
registerClipboardIpc({
  electron: {
    clipboard,
    dialog,
    getMainWindow,
    nativeImage
  },
  ipcMain
})
registerLogIpc({ ipcMain, log: rememberLog })
registerFilesIpc({
  electron: { dialog, getMainWindow },
  ipcMain
})
registerOnboardingAudioIpc({
  appRoot: APP_ROOT,
  spiritagentHome: SPIRITAGENT_HOME,
  ipcMain
})

// onRestored 在异步回调里才调用，先占位避免 session/runtime 互相前置。
let runnerHost: ReturnType<typeof createRunnerHost>

const sessionRuntime = createSessionRuntime({
  createSession: createBackendSession,
  desktopVersion: () => app.getVersion(),
  fetchImpl: electronFetch,
  log: rememberLog,
  onRestored: snapshot => authBroadcaster.onSessionRestored(snapshot),
  readStoredBackendUrl: () => readStoredBackendUrl(SPIRITAGENT_HOME),
  safeStorage,
  spiritagentHome: SPIRITAGENT_HOME,
  userDataDir: app.getPath('userData')
})

const assetDiskCache = createAssetDiskCache({
  defaultFetchFn: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
})

const sessionHistoryDiskCache = createSessionHistoryDiskCache({
  spiritagentHome: SPIRITAGENT_HOME
})

const voicePlaybackStore = createVoicePlaybackStore({ spiritagentHome: SPIRITAGENT_HOME })

registerConnectionIpc({
  assetDiskCache,
  defaultFetchTimeoutMs: DEFAULT_FETCH_TIMEOUT_MS,
  ensureBackend,
  fetchImpl: electronFetch,
  fetchJson: backendHttp.fetchJson,
  getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
  getSelectedAccountId: () => sessionRuntime.ensureBackendSession().getSelectedAccountId(),
  getMainWindow,
  ipcMain,
  mintWsTicket: backendHttp.mintWsTicket,
  resolvePathTimeoutMs
})
registerGatewayIpc({
  getMainWindow,
  ipcMain,
  rememberLog
})
registerMediaIpc({
  spiritagentHome: SPIRITAGENT_HOME,
  ensureBackend,
  fetchImpl: electronFetch,
  getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
  ipcMain,
  log: rememberLog
})

runnerHost = createRunnerHost({
  createReverseRpc,
  createRunnerBridge,
  createRunnerProcess,
  createRunnerWsServer,
  ensureBackendSession,
  fileExists,
  getMainWindow,
  rememberLog,
  spiritagentHome: SPIRITAGENT_HOME,
  taggedLogger: prefix => chunk => rememberLog(`${prefix} ${chunk}`)
})

const autoUpdater = createAutoUpdater({
  app,
  runtime: {
    ensureBackendSession,
    getRunnerBridge: () => runnerHost.getBridge(),
    spiritagentHome: SPIRITAGENT_HOME
  },
  createRunnerUpdater: deps => new RunnerUpdater(deps),
  fetchImpl: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
})

const authActions = registerAuthIpc({
  clearAccountCaches: async accountId => {
    await Promise.all([
      assetDiskCache.clear(accountId),
      sessionHistoryDiskCache.clear(accountId),
      voicePlaybackStore.clear(accountId)
    ])
  },
  deps: {
    autoStartBridge: () => runnerHost.autoStart(),
    autoStopBridge: () => runnerHost.autoStop(),
    restartBridge: () => runnerHost.restartForCurrentSession(),
    broadcastAuthChanged: authBroadcaster.broadcastAuthChanged,
    buildClientContext: () => sessionRuntime.buildClientContext(),
    ensureBackendSession,
    getSessionAfterRestore: () => sessionRuntime.getSessionAfterRestore(),
    log: rememberLog,
    onAccountIdentityChanged: async () => {
      try {
        await presentation?.accountChanged()
        await surfaces?.closeSurface()
      } finally {
        showMainWindow()
      }
    },
    resetBackendCache,
    spiritagentHome: SPIRITAGENT_HOME
  },
  ipcMain
})

registerSessionHistoryIpc({
  ensureBackendSession,
  ipcMain,
  sessionHistoryDiskCache
})
registerVoicePlaybackIpc({
  ensureBackendSession,
  ipcMain,
  store: voicePlaybackStore
})
runnerHost.registerIpc(ipcMain)
registerRunnerConfigIpc({
  ipcMain,
  isAuthorizedSender: event =>
    Boolean(surfaces?.isSurfaceSender('workbench', event.sender) || presentation?.isDesktopSender(event.sender))
})
registerSkillsIpc({
  spiritagentHome: SPIRITAGENT_HOME,
  getRunnerBridge: () => runnerHost.getBridge(),
  ipcMain
})
registerUpdateIpc({
  electron: { app },
  feed: autoUpdater,
  ipcMain,
  isInstallSender: sender =>
    Boolean(surfaces?.isSurfaceSender('living', sender) || presentation?.isDesktopSender(sender)),
  prepareInstall: () => presentation?.stop() ?? Promise.resolve(),
  markQuitting: () => appQuit.markQuitting()
})

registerSpriteIpc({
  deps: {
    getRunnerBridge: () => runnerHost.getBridge(),
    getSpriteWindow: getMainWindow,
    getStageWindow: () =>
      presentation?.getState().effectiveMode === 'desktop' ? presentation.getStageWindow() : getMainWindow(),
    isDesktopSender: sender => presentation?.isStageSender(sender) ?? false,
    getUserDataDir: () => app.getPath('userData'),
    log: rememberLog,
    screen
  },
  ipcMain
})

registerDesktopAccountIpc({
  deps: {
    ensureBackendSession,
    isDesktopSender: sender => presentation?.isDesktopSender(sender) ?? false,
    onLeavingDesktop: () => presentation?.accountChanged() ?? Promise.resolve(),
    openMainWindow: () => {
      showMainWindow()
      sendToWindow(mainWindow, IPC.event.trayActivate)
    },
    quitApp: () => app.quit(),
    switchAccount: authActions.switchAccount
  },
  ipcMain
})

void app.whenReady().then(async () => {
  if (appQuit.isQuitting()) {
    return
  }

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
  windowHandlers.registerPowerResumeListeners()
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
    Tray
  }

  registerSingleInstanceForwarder(trayDeps)
  // 早期第二实例事件须在转发器写入托盘依赖后兑现：showMainWindow 依赖它。
  singleInstance.replayEarlySecondInstance(showMainWindow)

  installTray({ ...trayDeps, getIsQuitting: () => appQuit.isQuitting(), surfaces: surfaces ?? undefined })

  if (!IS_PACKAGED && process.connected) {
    process.send?.({ event: 'spiritagent:dev-ready' }, error => {
      if (error) {
        rememberLog(`[dev] launcher readiness failed: ${error.message}`)
      }
    })
  }

  app.on('activate', () => showMainWindow())
})

const appQuit = installAppQuit({
  app,
  cleanupShortcuts,
  destroyTray,
  flushConfig: () => configSync.flush(),
  flushLog: () => desktopLogger.flushSync(),
  flushPlayback: () => voicePlaybackStore.flush(),
  log: rememberLog,
  restoreDesktop: () => presentation?.stop() ?? Promise.resolve(),
  stopRunner: () => runnerHost.getBridge()?.stop({ reason: 'app-quit' }) ?? Promise.resolve()
})

if (!IS_PACKAGED && process.connected) {
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
