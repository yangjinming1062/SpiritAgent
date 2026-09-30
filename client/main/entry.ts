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
import { createAuthBroadcaster, registerAuthIpc } from './ipc/auth'
import { registerClipboardIpc } from './ipc/clipboard'
import { registerConnectionIpc } from './ipc/connection'
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
import { installAppQuit } from './lifecycle/app-quit'
import { createAutoUpdater } from './lifecycle/auto-updater'
import { syncBundledSkills } from './lifecycle/bundled-skills'
import { createDesktopLogger } from './lifecycle/desktop-log'
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
  hideMainWindow,
  installCloseInterceptor,
  installTray,
  rebuildTrayMenu,
  registerSingleInstanceForwarder,
  showMainWindow
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
  DATA_URL_READ_MAX_BYTES,
  DEFAULT_CSP_POLICY,
  DEFAULT_FETCH_TIMEOUT_MS,
  DEV_CSP_POLICY,
  resolvePathTimeoutMs,
  resolveReadableFileForIpc
} from './security/hardening'
import { resolveDesktopHome } from './security/paths'
import { buildClientContext } from './shared/client-context'
import { readStoredBackendUrl } from './shared/config'
import { createConfigSync, uiThemeFromConfig } from './shared/lib/config-sync'
import * as runnerConfigStore from './shared/lib/runner-config-store'
import { mimeTypeForPath } from './shared/mime'
import { broadcastToAllWindows, errorMessage, fileExists, sendToWindow } from './shared/utils'

const DEV_SERVER = process.env.SPIRITAGENT_DESKTOP_DEV_SERVER
const IS_PACKAGED = app.isPackaged
const IS_MAC = process.platform === 'darwin'
const APP_ROOT = app.getAppPath()

const singleInstance = acquireSingleInstance(app)

// 模块级可变状态集中在此。
let mainWindow: BrowserWindow | null = null
let surfaces: null | SurfacesManager = null
let getAuthToken = (): string | null => null

const REMOTE_DISPLAY_REASON = applyChromiumSwitches(app)

const SPIRITAGENT_HOME = resolveDesktopHome()
fs.mkdirSync(SPIRITAGENT_HOME, { recursive: true })
app.setPath('userData', SPIRITAGENT_HOME)

const desktopLogger = createDesktopLogger({
  isPackaged: IS_PACKAGED,
  spiritagentHome: SPIRITAGENT_HOME
})

const rememberLog = (chunk: unknown): void => desktopLogger.rememberLog(chunk)

// 同步执行，先于会话恢复与 Runner 自动启动，使 Runner 与技能索引读到当前版本的随包技能。
syncBundledSkills({
  app,
  log: chunk => rememberLog(chunk),
  resourcesPath: process.resourcesPath,
  spiritagentHome: SPIRITAGENT_HOME
})

runnerConfigStore.init({ spiritagentHome: SPIRITAGENT_HOME })

const APP_NAME = '唤生'

const electronFetch = createElectronFetch(electronNet)

const backendHttp = createBackendHttp({
  app,
  electronNet,
  spiritagentHome: SPIRITAGENT_HOME
})

const { ensureBackend, resetBackendCache } = createEnsureBackend({
  appName: APP_NAME,
  backendHttp,
  logBootStep: message => rememberLog(`[boot] ${message}`),
  getAuthToken: () => getAuthToken(),
  getCurrentBaseUrl: () => sessionRuntime?.ensureBackendSession().getSession()?.baseUrl ?? null
})

// 云端配置同步协调器：backend user_settings 为真源，desktop-settings.json 是镜像
// （terminal/spiritagent 等机密与设备相关节仅本机，见 shared/lib/config-sync.ts）。
const configSync = createConfigSync({
  createBackendClient: ({ baseUrl }) => createBackendClient({ baseUrl, fetch: electronFetch }),
  ensureBackend: () => ensureBackend(),
  isRetryableError: isRetryableBackendError,
  log: chunk => rememberLog(chunk),
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
  rememberLog: (chunk: string) => rememberLog(chunk)
})

const getAppIconPath = createAppIconResolver(APP_ROOT)

applyAppIdentity(app, APP_NAME)

const zoomPersistence = createZoomPersistence({ app, rememberLog })
const surfaceCompanionPreferences = createSurfaceCompanionPreferences(app)

const contextMenuHelpers = createContextMenuHelpers({ electronNet })

const openExternalUrl = createOpenExternalUrl(chunk => rememberLog(chunk))

const menu = createMenu({
  app,
  appName: APP_NAME,
  getMainWindow: () => mainWindow,
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
  rememberLog: (chunk: string) => rememberLog(chunk),
  sendPowerResume: () => sendToWindow(mainWindow, IPC.event.powerResume),
  session,
  zoomPersistence
})

const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON
const PRELOAD_PATH = path.join(import.meta.dirname, 'preload.cjs')

const { createSpriteWindow, syncSpriteToDisplay } = createSpriteWindowFactory({
  app,
  getAppIconPath,
  getMainWindow: () => mainWindow,
  installCloseInterceptor,
  isMac: IS_MAC,
  preloadPath: PRELOAD_PATH,
  rememberLog: (chunk: string) => rememberLog(chunk),
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
  ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
  log: chunk => rememberLog(chunk),
  rebuildTrayMenu,
  resetPlaybackClaims: () => surfaces?.resetPlaybackClaims()
})

registerSystemIpc({
  electron: { app },
  ipcMain
})
registerUiThemeIpc({ ipcMain, log: chunk => rememberLog(chunk) })
registerPrefsIpc({ ipcMain, log: chunk => rememberLog(chunk) })

surfaces = createSurfacesManager({
  createWindow: createSurfaceWindow,
  getCompanionPreference: id => surfaceCompanionPreferences.get(id),
  getSpriteWindow: () => mainWindow,
  navigateWindow: navigateSurfaceWindow,
  rememberLog: (chunk: string) => rememberLog(chunk),
  saveCompanionPreference: (id, preference) => surfaceCompanionPreferences.set(id, preference),
  syncSpriteToDisplay
})
surfaces.registerIpcHandlers({ ipcMain })
surfaces.hydrateLastSurface()
registerShortcutsIpc({
  getMainWindow: () => mainWindow,
  hideMainWindow,
  ipcMain,
  rememberLog: chunk => rememberLog(chunk),
  showMainWindow: () => showMainWindow(),
  surfaces: surfaces ?? undefined
})
registerClipboardIpc({
  electron: {
    clipboard,
    dialog,
    getMainWindow: () => mainWindow,
    nativeImage
  },
  ipcMain
})
registerLogIpc({ ipcMain, log: chunk => rememberLog(chunk) })
registerFilesIpc({
  electron: { dialog, getMainWindow: () => mainWindow },
  hardening: { DATA_URL_READ_MAX_BYTES, resolveReadableFileForIpc },
  ipcMain,
  mimeTypeForPath
})
registerOnboardingAudioIpc({
  appRoot: APP_ROOT,
  spiritagentHome: SPIRITAGENT_HOME,
  hardening: { resolveReadableFileForIpc },
  ipcMain,
  mimeTypeForPath
})

const assetDiskCache = createAssetDiskCache({
  defaultFetchFn: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
})

const sessionHistoryDiskCache = createSessionHistoryDiskCache({
  spiritagentHome: SPIRITAGENT_HOME
})

registerConnectionIpc({
  assetDiskCache,
  defaultFetchTimeoutMs: DEFAULT_FETCH_TIMEOUT_MS,
  ensureBackend,
  fetchImpl: electronFetch,
  fetchJson: backendHttp.fetchJson,
  getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
  getMainWindow: () => mainWindow,
  ipcMain,
  mintWsTicket: backendHttp.mintWsTicket,
  resolvePathTimeoutMs
})
registerGatewayIpc({
  getMainWindow: () => mainWindow,
  ipcMain,
  rememberLog: chunk => rememberLog(chunk)
})
registerMediaIpc({
  spiritagentHome: SPIRITAGENT_HOME,
  ensureBackend,
  fetchImpl: electronFetch,
  getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
  ipcMain,
  log: chunk => rememberLog(chunk)
})

// 会话与 Runner 运行时分责：会话懒创建与 token 重接在 session-runtime；
// Runner 桥的持有、自动启停与 IPC 在 runner host。登录恢复经 authBroadcaster 广播后接回 host.autoStart。
// onRestored 异步回调里才调用；先占位避免 session/runtime 互相前置。
let runnerHost: ReturnType<typeof createRunnerHost>

const sessionRuntime = createSessionRuntime(
  {
    createSession: createBackendSession,
    desktopVersion: () => backendHttp.resolveSpiritAgentVersion(),
    errorMessage,
    fetchImpl: electronFetch,
    getTokenSetter: fn => {
      getAuthToken = fn
    },
    log: chunk => rememberLog(chunk),
    onRestored: snapshot => authBroadcaster.onSessionRestored(snapshot),
    readStoredBackendUrl: () => readStoredBackendUrl(SPIRITAGENT_HOME),
    safeStorage,
    spiritagentHome: SPIRITAGENT_HOME,
    userDataDir: app.getPath('userData')
  },
  buildClientContext
)

runnerHost = createRunnerHost({
  createReverseRpc,
  createRunnerBridge,
  createRunnerProcess,
  createRunnerWsServer,
  ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
  fileExists,
  getMainWindow: () => mainWindow,
  rememberLog: chunk => rememberLog(chunk),
  spiritagentHome: SPIRITAGENT_HOME,
  taggedLogger: prefix => chunk => rememberLog(`${prefix} ${chunk}`)
})

const autoUpdater = createAutoUpdater({
  app,
  runtime: {
    ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
    getRunnerBridge: () => runnerHost.getBridge(),
    spiritagentHome: SPIRITAGENT_HOME
  },
  createRunnerUpdater: ({ runtime: updaterRuntime, fetchImpl, log: updaterLog }) =>
    new RunnerUpdater({
      runtime: updaterRuntime,
      fetchImpl,
      log: updaterLog
    }),
  fetchImpl: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
})

const authActions = registerAuthIpc({
  clearLocalAssetCaches: async () => {
    await Promise.all([assetDiskCache.clear(), sessionHistoryDiskCache.clear()])
  },
  deps: {
    autoStartBridge: () => runnerHost.autoStart(),
    autoStopBridge: () => runnerHost.autoStop(),
    restartBridge: () => runnerHost.restartForCurrentSession(),
    broadcastAuthChanged: authBroadcaster.broadcastAuthChanged,
    buildClientContext: () => sessionRuntime.buildClientContext(),
    ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
    getSessionAfterRestore: () => sessionRuntime.getSessionAfterRestore(),
    log: chunk => rememberLog(chunk),
    onAccountIdentityChanged: async () => {
      try {
        await surfaces?.closeSurface()
      } finally {
        showMainWindow()
      }
    },
    rebuildTrayMenu,
    resetBackendCache,
    spiritagentHome: SPIRITAGENT_HOME
  },
  ipcMain
})

registerSessionHistoryIpc({
  ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
  ipcMain,
  sessionHistoryDiskCache
})
runnerHost.registerIpc(ipcMain)
registerRunnerConfigIpc({
  ipcMain,
  isAuthorizedSender: event => Boolean(surfaces?.isSurfaceSender('workbench', event.sender))
})
registerSkillsIpc({
  spiritagentHome: SPIRITAGENT_HOME,
  getRunnerBridge: () => runnerHost.getBridge(),
  ipcMain
})
registerUpdateIpc({
  broadcast: broadcastToAllWindows,
  electron: { app },
  feed: autoUpdater,
  ipcMain,
  isInstallSender: sender => Boolean(surfaces?.isSurfaceSender('living', sender)),
  markQuitting: () => appQuit.markQuitting()
})

registerSpriteIpc({
  deps: {
    getRunnerBridge: () => runnerHost.getBridge(),
    getSpriteWindow: () => mainWindow,
    getUserDataDir: () => app.getPath('userData'),
    log: chunk => rememberLog(chunk),
    screen
  },
  ipcMain
})

sessionRuntime.rewireAuthToken()

setTimeout(() => authBroadcaster.autoStartBridgeIfSignedIn(), 200).unref?.()

void app.whenReady().then(async () => {
  surfaces?.watchSystemEvents()
  menu.installApplicationMenu()
  windowHandlers.installMediaPermissions()
  windowHandlers.installContentSecurityPolicy()
  windowHandlers.configureSpellChecker(app)
  windowHandlers.registerPowerResumeListeners()
  syncShortcutsFromConfig()
  autoUpdater.setup()

  await autoUpdater.installPendingRunnerUpdate()
  createSpriteWindow()

  registerSingleInstanceForwarder({
    app,
    createWindow: createSpriteWindow,
    dialog,
    ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
    getAppIconPath,
    getMainWindow: () => mainWindow,
    Menu,
    nativeImage,
    rememberLog,
    removeAccount: authActions.removeAccount,
    switchAccount: authActions.switchAccount,
    Tray
  })
  // 早期第二实例事件须在转发器写入托盘依赖后兑现：showMainWindow 依赖它。
  singleInstance.replayEarlySecondInstance(showMainWindow)

  installTray({
    app,
    createWindow: createSpriteWindow,
    dialog,
    ensureBackendSession: () => sessionRuntime.ensureBackendSession(),
    getAppIconPath,
    getIsQuitting: () => appQuit.isQuitting(),
    getMainWindow: () => mainWindow,
    Menu,
    nativeImage,
    rememberLog,
    removeAccount: authActions.removeAccount,
    switchAccount: authActions.switchAccount,
    surfaces: surfaces ?? undefined,
    Tray
  })

  app.on('activate', () => showMainWindow())
})

const appQuit = installAppQuit({
  app,
  cleanupShortcuts,
  destroyTray,
  flushConfig: () => configSync.flush(),
  flushLog: () => desktopLogger.flushSync(),
  log: chunk => rememberLog(chunk),
  stopRunner: () => runnerHost.getBridge()?.stop({ reason: 'app-quit' }) ?? Promise.resolve()
})
