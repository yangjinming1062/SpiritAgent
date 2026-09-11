import fs from 'node:fs'
import path from 'node:path'

import {
  type DesktopAuthBroadcast,
  type DesktopAuthSnapshot,
  type DesktopSurfaceOpenPayload,
  IPC,
  type SurfaceId
} from '@ipc/contracts'
import { sleep } from '@runtime'
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
import { cleanupShortcuts, registerShortcutsIpc, syncShortcutsFromConfig } from './ipc/shortcuts'
import { registerSkillsIpc } from './ipc/skills'
import { readRestPosition, registerSpriteIpc } from './ipc/sprite'
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
let spriteBoundsListenerInstalled = false
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
const TITLEBAR_HEIGHT = 34
const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14

const WINDOW_BUTTON_POSITION = {
  x: 24,
  y: TITLEBAR_HEIGHT / 2 - MACOS_TRAFFIC_LIGHTS_HEIGHT / 2
}

const NATIVE_OVERLAY_BUTTON_WIDTH = 144

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
  ensureBackend: () => ensureBackend(),
  fetchImpl: (url, init) => electronNet.fetch(url, init),
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

const contextMenuHelpers = createContextMenuHelpers({ app, electronNet })

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

function getWindowButtonPosition(): { x: number; y: number } | null {
  if (!IS_MAC) {
    return null
  }

  return mainWindow?.getWindowButtonPosition?.() || WINDOW_BUTTON_POSITION
}

function getNativeOverlayWidth(): number {
  return IS_MAC ? 0 : NATIVE_OVERLAY_BUTTON_WIDTH
}

function getWindowState(): {
  isFullscreen: boolean
  nativeOverlayWidth: number
  windowButtonPosition: { x: number; y: number } | null
} {
  return {
    isFullscreen: Boolean(mainWindow?.isFullScreen?.()),
    nativeOverlayWidth: getNativeOverlayWidth(),
    windowButtonPosition: getWindowButtonPosition()
  }
}

function getAppIconPath(): null | string {
  return APP_ICON_PATHS.find(fileExists) || null
}

const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON

function applySpriteBounds(preferredOrigin?: { x: number; y: number }): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  // 贴住窗口当前覆盖的那块显示器（或启动时包含 preferredOrigin 的那块），
  // 而不是每次都弹回主显示器——精灵必须停留在用户拖到的那块显示器上。
  // 当原显示器被拔掉时，getDisplayMatching 会回退到最近的那块。
  const base = preferredOrigin
    ? { height: 1, width: 1, x: preferredOrigin.x, y: preferredOrigin.y }
    : mainWindow.getBounds()

  mainWindow.setBounds(screen.getDisplayMatching(base).workArea)
}

function createSpriteWindow(): void {
  const icon = getAppIconPath() || undefined
  mainWindow = new BrowserWindow({
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    height: 320,
    movable: false,
    resizable: false,
    show: false,
    skipTaskbar: true,
    // `type: 'panel'` 仅适用于 macOS（Cocoa NSPanel）；在 Win/Linux 上设置会输出 deprecation 警告。
    type: IS_MAC ? 'panel' : undefined,
    transparent: SPRITE_TRANSPARENT,
    webPreferences: {
      backgroundThrottling: false,
      contextIsolation: true,
      devTools: !app.isPackaged,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
      sandbox: true
    },
    width: 480
  })

  applySpriteBounds(readRestPosition(app.getPath('userData'))?.origin)
  mainWindow.setIgnoreMouseEvents(true, { forward: SPRITE_TRANSPARENT })

  // macOS 用 'screen-saver' z-band（位于 floating 之上，能压过 exclusive fullscreen 游戏）；
  // Win/Linux 回退 'floating'。Windows 的 exclusive fullscreen 完全绕过 DWM，
  // 伙伴窗口无法覆盖在上面（已记录的限制）。
  if (IS_MAC) {
    mainWindow.setAlwaysOnTop(true, 'screen-saver', 1)
  } else {
    mainWindow.setAlwaysOnTop(true, 'floating')
  }

  if (IS_MAC && icon) {
    app.dock?.setIcon(icon)
  }

  if (!spriteBoundsListenerInstalled) {
    spriteBoundsListenerInstalled = true
    screen.on('display-metrics-changed', () => applySpriteBounds())
  }

  windowHandlers.installStandardWindowHandlers(mainWindow)
  installCloseInterceptor(mainWindow)

  void mainWindow.loadURL(rendererUrlFor('sprite'))
  mainWindow.webContents.once('did-finish-load', () => {
    zoomPersistence.restorePersistedZoomLevel(mainWindow)
    bootProgress.broadcast()
    mainWindow?.showInactive()
  })
}

// 入口面互斥窗口工厂：living 是生活空间，workbench 是工作台。
// 形态按 SpiritAgent-客户端开发计划 §2.5 / §4 默认尺寸落定；具体内容（房间图、Run Rail 等）按阶段接入。
const SURFACE_DEFAULTS: Record<SurfaceId, { height: number; minHeight: number; minWidth: number; width: number }> = {
  living: { height: 720, minHeight: 560, minWidth: 880, width: 1080 },
  workbench: { height: 800, minHeight: 640, minWidth: 1264, width: 1532 }
}

async function createSurfaceWindow(id: SurfaceId, payload?: DesktopSurfaceOpenPayload): Promise<BrowserWindow> {
  const defaults = SURFACE_DEFAULTS[id]
  const icon = getAppIconPath() || undefined

  let initialX: number | undefined
  let initialY: number | undefined
  let initialWidth = defaults.width
  let initialHeight = defaults.height

  if (id === 'workbench') {
    const cursor = screen.getCursorScreenPoint()
    const display = screen.getDisplayNearestPoint(cursor)
    const wa = display.workArea

    initialWidth = Math.max(defaults.minWidth, Math.min(defaults.width, wa.width - 16))
    initialHeight = Math.max(defaults.minHeight, Math.min(defaults.height, wa.height - 16))
    initialX = Math.round(wa.x + Math.max(0, (wa.width - initialWidth) / 2))
    initialY = Math.round(wa.y + Math.max(0, (wa.height - initialHeight) / 2))
  }

  // 入口窗用 CSS 大圆角液态玻璃。Windows 亚克力与系统阴影按 HWND 矩形铺底，
  // 会在圆角切出的四角漏出灰底；关掉原生材质/圆角/阴影，圆角外像素保持真透明。
  const win = new BrowserWindow({
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    height: initialHeight,
    minHeight: defaults.minHeight,
    minWidth: defaults.minWidth,
    resizable: true,
    roundedCorners: false,
    show: false,
    skipTaskbar: false,
    title: id === 'living' ? `${APP_NAME} · 生活空间` : `${APP_NAME} · 工作台`,
    transparent: true,
    webPreferences: {
      backgroundThrottling: false,
      contextIsolation: true,
      devTools: !app.isPackaged,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
      sandbox: true
    },
    width: initialWidth,
    x: initialX,
    y: initialY
  })

  if (IS_MAC && icon) {
    app.dock?.setIcon(icon)
  }

  windowHandlers.installSurfaceWindowHandlers(win)

  win.on('close', () => {
    surfaces?.onWindowClosed(id, win)
    rebuildTrayMenu()
  })

  win.on('show', () => rebuildTrayMenu())
  win.on('hide', () => rebuildTrayMenu())

  await win.loadURL(surfaceLoadUrl(id, payload))
  zoomPersistence.restorePersistedZoomLevel(win)
  win.show()
  win.focus()

  return win
}

function surfaceLoadUrl(id: SurfaceId, payload?: DesktopSurfaceOpenPayload): string {
  const url = new URL(rendererUrlFor(id))

  if (payload?.view) {
    url.hash = `#/${payload.view}`
  }

  if (payload?.sessionId) {
    url.searchParams.set('sessionId', payload.sessionId)
  }

  return url.toString()
}

async function navigateSurfaceWindow(
  win: BrowserWindow,
  id: SurfaceId,
  payload: DesktopSurfaceOpenPayload
): Promise<void> {
  await win.loadURL(surfaceLoadUrl(id, payload))
  zoomPersistence.restorePersistedZoomLevel(win)
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
registerPrefsIpc({ ipcMain })

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
  ipcMain,
  writeComposerImage: contextMenuHelpers.writeComposerImage
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

registerConnectionIpc({
  assetDiskCache,
  defaultFetchTimeoutMs: DEFAULT_FETCH_TIMEOUT_MS,
  ensureBackend,
  fetchImpl: electronFetch,
  fetchJson: backendHttp.fetchJson,
  getBootProgressState: () => bootProgress.getState(),
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
  electronNet,
  spiritagentHome: SPIRITAGENT_HOME
})

registerAuthIpc({
  clearLocalAssetCaches: async () => {
    await Promise.all([assetDiskCache.clear(), modelDiskCache.clear()])
  },
  deps: bridgeDeps,
  ipcMain
})
registerRunnerIpc({ deps: bridgeDeps, ipcMain })
registerRunnerConfigIpc({ ipcMain })
registerSkillsIpc({ spiritagentHome: SPIRITAGENT_HOME, getRunnerBridge: () => bridgeDeps.runnerBridge, ipcMain })
registerUpdateIpc({
  electron: { app },
  getMainWindow: () => mainWindow,
  ipcMain,
  sendToMain
})

registerSpriteIpc({
  deps: { getSpriteWindow: () => mainWindow, getUserDataDir: () => app.getPath('userData'), screen },
  ipcMain
})

ipcMain.handle(IPC.invoke.runnerGetTools, async () => {
  const deadline = Date.now() + 6000

  while (Date.now() < deadline) {
    const bridge = bridgeDeps.runnerBridge

    if (bridge) {
      const tools = bridge.getTools()

      if (tools.length > 0) {
        return tools
      }

      const status = bridge.getStatus()

      if (status.phase === 'error' || status.phase === 'stopped') {
        return []
      }
    }

    await sleep(100)
  }

  return bridgeDeps.runnerBridge?.getTools() || []
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

  if (bridgeDeps.runnerBridge) {
    try {
      void bridgeDeps.runnerBridge.stop({ reason: 'app-quit' })
    } catch (error) {
      const msg = errorMessage(error)
      rememberLog(`[runner-bridge] quit cleanup failed: ${msg}`)
    }
  }

  desktopLogger.flushSync()
})

app.on('window-all-closed', () => {
  if (bridgeDeps.isQuitting && !IS_MAC) {
    app.quit()
  }
})
