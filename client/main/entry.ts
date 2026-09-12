import fs from 'node:fs'
import path from 'node:path'

import { type DesktopAuthBroadcast, type DesktopAuthSnapshot, IPC } from '@ipc/contracts'
import {
  app,
  BrowserWindow,
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
import log from 'electron-log/main'

import { BackendRequestError, createBackendClient } from './backend/client'
import { createEnsureBackend } from './backend/ensure-backend'
import { createBackendHttp } from './backend/http'
import { createBackendSession, type SessionSnapshot } from './backend/session'
import { createAssetDiskCache } from './ipc/asset-disk-cache'
import { registerAuthIpc } from './ipc/auth'
import { registerClipboardIpc } from './ipc/clipboard'
import { registerConnectionIpc } from './ipc/connection'
import { registerFilesIpc } from './ipc/files'
import { registerGatewayIpc } from './ipc/gateway'
import { registerLogIpc } from './ipc/log'
import { registerMediaIpc } from './ipc/media'
import { createModelDiskCache } from './ipc/model-disk-cache'
import { registerOnboardingAudioIpc } from './ipc/onboarding-audio'
import { registerPrefsIpc } from './ipc/prefs'
import { autoStartBridge, autoStopBridge, registerRunnerIpc } from './ipc/runner'
import { registerRunnerConfigIpc } from './ipc/runner-config'
import { registerSessionHistoryIpc } from './ipc/session-history'
import { createSessionHistoryDiskCache } from './ipc/session-history-disk-cache'
import { cleanupShortcuts, registerShortcutsIpc, syncShortcutsFromConfig } from './ipc/shortcuts'
import { registerSkillsIpc } from './ipc/skills'
import { registerSpriteIpc } from './ipc/sprite'
import { registerSystemIpc } from './ipc/system'
import { registerUiThemeIpc } from './ipc/ui-theme'
import { registerUpdateIpc } from './ipc/update'
import { createAutoUpdater } from './lifecycle/auto-updater'
import { createBootProgressMachine } from './lifecycle/boot-progress'
import { createDesktopLogger } from './lifecycle/desktop-log'
import { registerMediaProtocol, registerMediaProtocolScheme } from './lifecycle/media-protocol'
import { createMenu } from './lifecycle/menu'
import { createOpenExternalUrl } from './lifecycle/open-external-url'
import { detectRemoteDisplay } from './lifecycle/platform'
import { createRendererPaths, unpackedPathFor } from './lifecycle/renderer-paths'
import { createSpriteWindowFactory, getWindowState as readSpriteWindowState } from './lifecycle/sprite-window'
import { createSurfaceWindowFactory } from './lifecycle/surface-window'
import { createSurfacesManager, type SurfacesManager } from './lifecycle/surfaces'
import {
  destroyTray,
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
import { createBridgeDeps } from './runner/bridge-deps'
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
  resolveReadableFileForIpc,
  resolveTimeoutMs
} from './security/hardening'
import { spiritagentHome } from './security/paths'
import { buildClientContext } from './shared/client-context'
import { readStoredBackendUrl } from './shared/config'
import { createConfigSync } from './shared/lib/config-sync'
import * as runnerConfigStore from './shared/lib/runner-config-store'
import { mimeTypeForPath } from './shared/mime'
import {
  atomicWriteFile,
  broadcastToAllWindows,
  errorMessage,
  fileExists,
  hideAndSkipTaskbar,
  sendToMain
} from './shared/utils'

const USER_DATA_OVERRIDE = process.env.SPIRITAGENT_DESKTOP_USER_DATA_DIR

const DEV_SERVER = process.env.SPIRITAGENT_DESKTOP_DEV_SERVER
const IS_PACKAGED = app.isPackaged
const IS_MAC = process.platform === 'darwin'
const APP_ROOT = app.getAppPath()

if (process.env.SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK !== '1') {
  if (!app.requestSingleInstanceLock()) {
    app.exit(0)
  }
}

// `whenReady` 内的 `createSpriteWindow()` 之前若收到第二实例事件，会被 Electron 直接丢弃。
// 顶层先挂一个轻量 listener 把事件折叠成标志；完整 forwarder 注册后再兑现一次。
// 模块级可变状态集中在此（原则五）。
let pendingSecondInstance = false
let mainWindow: BrowserWindow | null = null
let surfaces: null | SurfacesManager = null
let getAuthToken = (): string | null => null

const onEarlySecondInstance = (): void => {
  pendingSecondInstance = true
}

app.on('second-instance', onEarlySecondInstance)

const REMOTE_DISPLAY_REASON = detectRemoteDisplay()

if (REMOTE_DISPLAY_REASON) {
  app.disableHardwareAcceleration()
  app.commandLine.appendSwitch('disable-gpu-compositing')
  console.log(
    `[spiritagent] remote display detected (${REMOTE_DISPLAY_REASON}); disabling GPU hardware acceleration to prevent flicker`
  )
}

app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('disable-background-timer-throttling')

function resolveSpiritAgentHome(): string {
  if (USER_DATA_OVERRIDE) {
    return path.join(path.resolve(USER_DATA_OVERRIDE), 'spiritagent-home')
  }

  return spiritagentHome()
}

const SPIRITAGENT_HOME = resolveSpiritAgentHome()
fs.mkdirSync(SPIRITAGENT_HOME, { recursive: true })
app.setPath('userData', SPIRITAGENT_HOME)

const desktopLogger = createDesktopLogger({
  isPackaged: IS_PACKAGED,
  spiritagentHome: SPIRITAGENT_HOME
})

const rememberLog = (chunk: unknown): void => desktopLogger.rememberLog(chunk)

runnerConfigStore.init({ spiritagentHome: SPIRITAGENT_HOME })

const APP_NAME = '唤生'

const backendHttp = createBackendHttp({
  app,
  electronNet,
  rememberLog: (chunk: string) => rememberLog(chunk),
  spiritagentHome: SPIRITAGENT_HOME
})

const bootProgress = createBootProgressMachine({
  getMainWindow: () => mainWindow,
  rememberLog: (chunk: string) => rememberLog(chunk)
})

const { ensureBackend, resetBackendCache, setCachedWsUrl } = createEnsureBackend({
  appName: APP_NAME,
  backendHttp,
  bootProgress,
  getAuthToken: () => getAuthToken(),
  getWindowState
})

// 云端配置同步协调器：backend user_settings 为真源，desktop-settings.json 是镜像
// （terminal/spiritagent 等机密与设备相关节仅本机，见 shared/lib/config-sync.ts）。
const configSync = createConfigSync({
  createBackendClient: ({ baseUrl }) =>
    createBackendClient({ baseUrl, fetch: (url, init) => electronNet.fetch(url, init) }),
  ensureBackend: () => ensureBackend(),
  isRetryableError: error => error instanceof BackendRequestError && (error.isNetwork || error.isServerError),
  log: chunk => rememberLog(chunk),
  onHydrated: payload => {
    // payload 已由 config-sync 的 buildPrefsHydratedFromConfig 统一构造（含主题规范化）。
    const theme = payload.ui.theme

    syncShortcutsFromConfig()

    broadcastToAllWindows(IPC.event.prefsHydrated, payload)

    if (theme) {
      broadcastToAllWindows(IPC.event.uiThemeChanged, { theme })
    }

    rebuildTrayMenu()
  }
})

runnerConfigStore.setCloudSync(configSync)

const { rendererUrlFor } = createRendererPaths({
  appRoot: APP_ROOT,
  devServer: DEV_SERVER,
  isPackaged: IS_PACKAGED,
  rememberLog: (chunk: string) => rememberLog(chunk)
})

const APP_ICON_PATHS = [
  path.join(APP_ROOT, 'assets', 'icon.png'),
  path.join(process.resourcesPath, 'icon.ico'),
  path.join(unpackedPathFor(APP_ROOT), 'icon.ico')
]

app.setName(APP_NAME)

if (process.platform === 'win32') {
  app.setAppUserModelId('io.spiritagent.agent')
}

app.setAboutPanelOptions({
  applicationName: APP_NAME,
  applicationVersion: backendHttp.resolveSpiritAgentVersion(),
  copyright: `Copyright © 2026 ${APP_NAME}`
})

registerMediaProtocolScheme()

const zoomPersistence = createZoomPersistence({ app, rememberLog })

const contextMenuHelpers = createContextMenuHelpers({ electronNet })

const openExternalUrl = createOpenExternalUrl(chunk => rememberLog(chunk))

const menu = createMenu({
  app,
  appName: APP_NAME,
  getMainWindow: () => mainWindow,
  isMac: IS_MAC,
  menu: Menu,
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
  sendPowerResume: () => sendToMain(mainWindow, IPC.event.powerResume),
  session,
  zoomPersistence
})

function getAppIconPath(): null | string {
  return APP_ICON_PATHS.find(fileExists) || null
}

const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON
const PRELOAD_PATH = path.join(import.meta.dirname, 'preload.cjs')

const { createSpriteWindow } = createSpriteWindowFactory({
  app,
  bootProgress,
  getAppIconPath,
  getMainWindow: () => mainWindow,
  installCloseInterceptor,
  isMac: IS_MAC,
  preloadPath: PRELOAD_PATH,
  rendererUrlFor,
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
  getSurfaces: () => surfaces,
  isMac: IS_MAC,
  preloadPath: PRELOAD_PATH,
  rebuildTrayMenu,
  rendererUrlFor,
  windowHandlers,
  zoomPersistence
})

function getWindowState(): {
  isFullscreen: boolean
  nativeOverlayWidth: number
  windowButtonPosition: { x: number; y: number } | null
} {
  return readSpriteWindowState({ getMainWindow: () => mainWindow, isMac: IS_MAC })
}

function broadcastAuthChanged(snapshot: null | SessionSnapshot): void {
  rebuildTrayMenu()

  const authenticated = Boolean(snapshot?.hasToken)

  const authSnapshot: DesktopAuthSnapshot | null =
    authenticated && snapshot
      ? {
          baseUrl: snapshot.baseUrl,
          hasToken: snapshot.hasToken,
          tokenExpiresAt: snapshot.tokenExpiresAt,
          user: snapshot.user?.username ? { username: snapshot.user.username } : null
        }
      : null

  const payload: DesktopAuthBroadcast = { authenticated, snapshot: authSnapshot }

  // 用户身份变化触发配置水合（登录/换号；登出只停摆待写）。
  configSync.handleAuthUserChanged(authenticated ? (snapshot?.user?.id ?? null) : null)

  broadcastToAllWindows(IPC.event.authChanged, payload)
}

registerSystemIpc({
  electron: { app },
  ipcMain
})
registerUiThemeIpc({ ipcMain })
registerPrefsIpc({ ipcMain, onLanguageChanged: () => rebuildTrayMenu() })

surfaces = createSurfacesManager({
  createWindow: createSurfaceWindow,
  navigateWindow: navigateSurfaceWindow,
  rememberLog: (chunk: string) => rememberLog(chunk),
  syncSpriteToDisplay: display => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      const currentMatching = screen.getDisplayMatching(mainWindow.getBounds())

      if (currentMatching.id !== display.id) {
        mainWindow.setBounds(display.workArea)
      }
    }
  }
})
surfaces.registerIpcHandlers({ ipcMain })
surfaces.hydrateLastSurface()
registerShortcutsIpc({
  getMainWindow: () => mainWindow,
  hideMainWindow: () => {
    hideAndSkipTaskbar(mainWindow)
    rebuildTrayMenu()
  },
  ipcMain,
  rememberLog: chunk => rememberLog(chunk),
  showMainWindow: () => showMainWindow(),
  surfaces: surfaces ?? undefined
})
registerClipboardIpc({
  electron: { clipboard },
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
  app,
  appRoot: APP_ROOT,
  spiritagentHome: SPIRITAGENT_HOME,
  hardening: { resolveReadableFileForIpc },
  ipcMain,
  mimeTypeForPath
})
const electronFetch = electronNet.fetch as unknown as typeof globalThis.fetch

const modelDiskCache = createModelDiskCache({
  defaultFetchFn: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
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
  getBootProgressState: () => bootProgress.getState(),
  getMainWindow: () => mainWindow,
  ipcMain,
  mintWsTicket: backendHttp.mintWsTicket,
  modelDiskCache,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  setCachedWsUrl
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
  ipcMain,
  log: chunk => rememberLog(chunk)
})

// BridgeDeps 工厂接收所有依赖为参数；它本身不再持有模块顶层 free variable，
// 这样既保留 36 字段契约，又把"对象工厂 vs 对象字面量"的差异常规化为参数注入。
const bridgeDeps = createBridgeDeps({
  app,
  atomicWriteFile,
  autoStartBridge,
  autoStopBridge,
  backendHttp,
  broadcastAuthChanged,
  buildClientContext,
  createBackendSession,
  createReverseRpc,
  createRunnerBridge,
  createRunnerProcess,
  createRunnerWsServer,
  electronNet,
  errorMessage,
  fileExists,
  getAuthToken: {
    getter: () => getAuthToken(),
    setter: fn => {
      getAuthToken = fn
    }
  },
  getMainWindow: () => mainWindow,
  getSpriteWindow: () => mainWindow,
  readStoredBackendUrl,
  rebuildTrayMenu,
  rememberLog: (chunk: string) => rememberLog(chunk),
  resetBackendCache,
  safeStorage,
  spiritagentHome: SPIRITAGENT_HOME
})

const autoUpdater = createAutoUpdater({
  app,
  appRoot: APP_ROOT,
  bridgeDeps,
  createRunnerUpdater: ({ bridgeDeps: deps, fetchImpl }) =>
    new RunnerUpdater({
      bridgeDeps: deps,
      fetchImpl: fetchImpl as typeof globalThis.fetch
    }),
  electronNet,
  spiritagentHome: SPIRITAGENT_HOME
})

registerAuthIpc({
  clearLocalAssetCaches: async () => {
    await Promise.all([assetDiskCache.clear(), modelDiskCache.clear(), sessionHistoryDiskCache.clear()])
  },
  deps: bridgeDeps,
  ipcMain
})
registerSessionHistoryIpc({
  ensureBackendSession: () => bridgeDeps.ensureBackendSession(),
  ipcMain,
  sessionHistoryDiskCache
})
registerRunnerIpc({ deps: bridgeDeps, ipcMain })
registerRunnerConfigIpc({
  ipcMain,
  isAuthorizedSender: event => {
    const win = BrowserWindow.fromWebContents(event.sender)

    return Boolean(win && surfaces?.isSurfaceWindow('workbench', win))
  }
})
registerSkillsIpc({ spiritagentHome: SPIRITAGENT_HOME, getRunnerBridge: () => bridgeDeps.runnerBridge, ipcMain })
registerUpdateIpc({
  electron: { app },
  getMainWindow: () => mainWindow,
  ipcMain,
  isFeedConfigured: () => autoUpdater.isFeedConfigured(),
  sendToMain
})

registerSpriteIpc({
  deps: { getSpriteWindow: () => mainWindow, getUserDataDir: () => app.getPath('userData'), screen },
  ipcMain
})

bridgeDeps.rewireAuthToken()

setTimeout(() => {
  if (bridgeDeps.ensureBackendSession().getSession()?.hasToken) {
    autoStartBridge(bridgeDeps)
  }
}, 200).unref?.()

void app.whenReady().then(async () => {
  if (IS_MAC) {
    Menu.setApplicationMenu(menu.buildApplicationMenu())
  } else {
    Menu.setApplicationMenu(null)
  }

  windowHandlers.installMediaPermissions()
  windowHandlers.installContentSecurityPolicy()
  registerMediaProtocol(SPIRITAGENT_HOME)
  windowHandlers.configureSpellChecker(app)
  windowHandlers.registerPowerResumeListeners()
  autoUpdater.setup()

  await autoUpdater
    .getRunnerUpdater()
    .installPending()
    .catch(err => {
      log.warn('runner installPending failed:', errorMessage(err))
    })
  createSpriteWindow()

  registerSingleInstanceForwarder({
    app,
    bridgeDeps,
    createWindow: createSpriteWindow,
    getAppIconPath,
    Menu,
    nativeImage,
    rememberLog,
    Tray
  })

  app.removeListener('second-instance', onEarlySecondInstance)

  if (pendingSecondInstance) {
    pendingSecondInstance = false
    showMainWindow()
  }

  installTray({
    app,
    bridgeDeps,
    createWindow: createSpriteWindow,
    getAppIconPath,
    Menu,
    nativeImage,
    rememberLog,
    surfaces: surfaces ?? undefined,
    Tray
  })

  app.on('activate', () => {
    const win = bridgeDeps.getMainWindow()

    if (!win || win.isDestroyed()) {
      createSpriteWindow()
    } else {
      showMainWindow()
    }
  })
})

app.on('before-quit', () => {
  bridgeDeps.setQuitting(true)
  destroyTray()
  cleanupShortcuts()

  // 尽力而为的收尾上云；进程先退也不丢——下次启动水合的键级播种会把遗留编辑补传。
  void configSync.flush()

  desktopLogger.flushSync()
})

// will-quit 有界等待 Runner 收尾，避免 fire-and-forget 留下孤儿子进程。
let willQuitCleanupDone = false

app.on('will-quit', event => {
  if (willQuitCleanupDone) {
    return
  }

  willQuitCleanupDone = true
  event.preventDefault()

  const stopPromise = bridgeDeps.runnerBridge ? bridgeDeps.runnerBridge.stop({ reason: 'app-quit' }) : Promise.resolve()
  const timeoutMs = 3000

  void Promise.race([
    stopPromise.catch(error => {
      rememberLog(`[runner-bridge] quit cleanup failed: ${errorMessage(error)}`)
    }),
    new Promise(resolve => {
      const timer = setTimeout(resolve, timeoutMs)

      if (typeof timer.unref === 'function') {
        timer.unref()
      }
    })
  ]).then(() => {
    desktopLogger.flushSync()
    app.exit(0)
  })
})

app.on('window-all-closed', () => {
  // 常驻托盘：关窗不退出。真正退出由托盘/菜单的 app.quit → before-quit/will-quit 驱动；
  // 这里不能再调 app.quit，否则会重入清理并双跑 runner stop。
})
