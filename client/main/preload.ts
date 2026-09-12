import type { MemoryToolScope } from '@ipc/contracts'
import {
  type AttachmentVideoUploadPayload,
  type DesktopActivatePayload,
  type DesktopAuthBroadcast,
  type DesktopBootProgress,
  type DesktopGatewayEvent,
  type DesktopGatewayRpcRequest,
  type DesktopGatewayRpcResponse,
  type DesktopGatewayState,
  type DesktopPrefsHydrated,
  type DesktopRunnerStatusEvent,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  type DesktopUiThemeBroadcast,
  type DesktopUpdateEvent,
  IPC,
  type IpcEventChannel,
  type IpcEventContract,
  type MediaSttPayload,
  type MediaTtsPayload,
  type RunnerConfigPatch,
  type SessionHistorySnapshot,
  type SpiritAgentApiRequest,
  type SpiritAgentPrefsSet,
  type SpiritAgentSelectPathsOptions,
  type SpiritAgentUiTheme
} from '@ipc/contracts'
import { contextBridge, ipcRenderer, type IpcRendererEvent, webUtils } from 'electron'

// Electron 32+ 移除了 File.path——桌面文件拖拽的真实路径只能经 webUtils.getPathForFile 拿到。
// 解析成功即写入主进程可读白名单；不再向渲染层暴露 registerUserSelectedPaths，
// 防止 XSS 用任意路径自授后 readFileDataUrl 外传。
contextBridge.exposeInMainWorld('spiritagentWebUtils', {
  getPathForFile: (file: File): string => {
    const filePath = webUtils.getPathForFile(file)

    if (filePath) {
      void ipcRenderer.invoke(IPC.invoke.registerUserSelectedPaths, [filePath]).catch(() => {})
    }

    return filePath
  }
})

// 订阅主进程单方向事件：listener 解构 payload，丢弃 IpcRendererEvent；
// 返回卸载函数，调用方可在 useEffect 清理时调它取消订阅。
function subscribe<C extends IpcEventChannel>(
  channel: C,
  callback: (...payload: IpcEventContract[C]) => void
): () => void {
  const listener = (_event: IpcRendererEvent, ...payload: IpcEventContract[C]): void => {
    ;(callback as (...args: unknown[]) => void)(...payload)
  }

  ipcRenderer.on(channel, listener)

  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('spiritagent', {
  activate: (payload: DesktopActivatePayload) => ipcRenderer.invoke(IPC.invoke.authActivate, payload),
  api: (request: SpiritAgentApiRequest) => ipcRenderer.invoke(IPC.invoke.api, request),
  apiAsset: (request: { preferCache?: boolean; cacheOnly?: boolean; contentHash?: string; url: string }) =>
    ipcRenderer.invoke(IPC.invoke.apiAsset, request),
  apiAssetBuffer: (request: { preferCache?: boolean; contentHash?: string; url: string }) =>
    ipcRenderer.invoke(IPC.invoke.apiAssetBuffer, request),
  apiAssetModelUrl: (request: { contentHash?: string; url: string }) =>
    ipcRenderer.invoke(IPC.invoke.apiAssetModelUrl, request),
  sessionHistory: {
    get: (sessionId: string) => ipcRenderer.invoke(IPC.invoke.sessionHistoryGet, sessionId),
    save: (sessionId: string, snapshot: SessionHistorySnapshot) =>
      ipcRenderer.invoke(IPC.invoke.sessionHistorySave, sessionId, snapshot),
    remove: (sessionId: string) => ipcRenderer.invoke(IPC.invoke.sessionHistoryRemove, sessionId)
  },
  getBootProgress: () => ipcRenderer.invoke(IPC.invoke.bootProgressGet),
  getConnection: () => ipcRenderer.invoke(IPC.invoke.connection),
  getGatewayWsUrl: () => ipcRenderer.invoke(IPC.invoke.gatewayWsUrl),
  gatewayRequest: (payload: { method: string; params?: Record<string, unknown> }) =>
    ipcRenderer.invoke(IPC.invoke.gatewayRequest, payload),
  gatewayGetState: () => ipcRenderer.invoke(IPC.invoke.gatewayGetState),
  gatewayBroadcastState: (state: DesktopGatewayState) => ipcRenderer.send(IPC.send.gatewayBroadcastState, { state }),
  gatewayBroadcastEvent: (event: DesktopGatewayEvent) => ipcRenderer.send(IPC.send.gatewayBroadcastEvent, { event }),
  gatewayRpcReply: (payload: DesktopGatewayRpcResponse) => ipcRenderer.send(IPC.send.gatewayRpcReply, payload),
  onGatewayStateChanged: (cb: (payload: { state: DesktopGatewayState }) => void) =>
    subscribe(IPC.event.gatewayStateChanged, cb),
  onGatewayEvent: (cb: (payload: { event: DesktopGatewayEvent }) => void) => subscribe(IPC.event.gatewayEvent, cb),
  onGatewayRpcDispatch: (cb: (payload: DesktopGatewayRpcRequest) => void) =>
    subscribe(IPC.event.gatewayRpcDispatch, cb),
  getSession: () => ipcRenderer.invoke(IPC.invoke.authGetSession),
  getVersion: () => ipcRenderer.invoke(IPC.invoke.version),
  log: (payload: { args: unknown[]; level: 'error' | 'info' | 'warn'; scope: string }) =>
    ipcRenderer.invoke(IPC.invoke.logEmit, payload),
  logout: () => ipcRenderer.invoke(IPC.invoke.authLogout),
  media: {
    onboardingAudio: {
      read: (tag: string) => ipcRenderer.invoke(IPC.invoke.onboardingAudioRead, tag)
    },
    stt: (payload: MediaSttPayload) => ipcRenderer.invoke(IPC.invoke.mediaStt, payload),
    tts: (payload: MediaTtsPayload) => ipcRenderer.invoke(IPC.invoke.mediaTts, payload)
  },
  onAuthChanged: (cb: (payload: DesktopAuthBroadcast) => void) => subscribe(IPC.event.authChanged, cb),
  onBootProgress: (cb: (payload: DesktopBootProgress) => void) => subscribe(IPC.event.bootProgress, cb),
  onPowerResume: (cb: () => void) => subscribe(IPC.event.powerResume, cb),
  onRunnerStatus: (cb: (payload: DesktopRunnerStatusEvent) => void) => subscribe(IPC.event.runnerStatus, cb),
  onSessionExpired: (cb: () => void) => subscribe(IPC.event.authSessionExpired, cb),
  onTrayActivate: (cb: () => void) => subscribe(IPC.event.trayActivate, cb),
  onTrayLogout: (cb: () => void) => subscribe(IPC.event.trayLogout, cb),
  onTrayResetPosition: (cb: () => void) => subscribe(IPC.event.trayResetPosition, cb),
  onPrefsHydrated: (cb: (payload: DesktopPrefsHydrated) => void) => subscribe(IPC.event.prefsHydrated, cb),
  onUiThemeChanged: (cb: (payload: DesktopUiThemeBroadcast) => void) => subscribe(IPC.event.uiThemeChanged, cb),
  readFileDataUrl: (filePath: string) => ipcRenderer.invoke(IPC.invoke.readFileDataUrl, filePath),
  readImageForAttach: (filePath: string) => ipcRenderer.invoke(IPC.invoke.readImageForAttach, filePath),
  uploadVideoForAttach: (payload: AttachmentVideoUploadPayload) =>
    ipcRenderer.invoke(IPC.invoke.mediaVideoUpload, payload),
  refreshSession: () => ipcRenderer.invoke(IPC.invoke.authRefresh),
  runnerCancel: () => ipcRenderer.invoke(IPC.invoke.runnerCancel),
  runnerConfig: {
    patch: (patch: RunnerConfigPatch) => ipcRenderer.invoke(IPC.invoke.runnerConfigPatch, patch),
    read: () => ipcRenderer.invoke(IPC.invoke.runnerConfigRead),
    write: (configString: string) => ipcRenderer.invoke(IPC.invoke.runnerConfigWrite, configString)
  },
  runnerGetState: () => ipcRenderer.invoke(IPC.invoke.runnerGetState),
  runnerGetTools: () => ipcRenderer.invoke(IPC.invoke.runnerGetTools),
  runnerInvoke: (name: string, args: Record<string, unknown>, skillScope?: MemoryToolScope) =>
    ipcRenderer.invoke(IPC.invoke.runnerInvoke, name, args, skillScope),
  selectPaths: (options?: SpiritAgentSelectPathsOptions) => ipcRenderer.invoke(IPC.invoke.selectPaths, options),
  prefs: {
    set: (payload: SpiritAgentPrefsSet) => ipcRenderer.send(IPC.send.prefsSet, payload)
  },
  setUiTheme: (payload: SpiritAgentUiTheme) => ipcRenderer.send(IPC.send.uiTheme, payload),
  shortcuts: {
    get: () => ipcRenderer.invoke(IPC.invoke.shortcutsGet),
    onChanged: (cb: (payload: DesktopShortcutsState) => void) => subscribe(IPC.event.shortcutsChanged, cb),
    set: (payload: DesktopShortcutsSetPayload) => ipcRenderer.invoke(IPC.invoke.shortcutsSet, payload)
  },
  surface: {
    close: () => ipcRenderer.invoke(IPC.invoke.surfaceClose),
    getState: () => ipcRenderer.invoke(IPC.invoke.surfaceGetState),
    isMaximized: () => ipcRenderer.invoke(IPC.invoke.surfaceIsMaximized),
    maximize: () => ipcRenderer.invoke(IPC.invoke.surfaceMaximize),
    minimize: () => ipcRenderer.invoke(IPC.invoke.surfaceMinimize),
    onChanged: (cb: (payload: DesktopSurfaceChangedEvent) => void) => subscribe(IPC.event.surfaceChanged, cb),
    open: (payload: DesktopSurfaceOpenPayload) => ipcRenderer.invoke(IPC.invoke.surfaceOpen, payload),
    setIgnoreMouseEvents: (payload: { forward?: boolean; ignore: boolean }) =>
      ipcRenderer.invoke(IPC.invoke.surfaceSetIgnoreMouseEvents, payload)
  },
  chat: {
    onPendingFeed: (cb: (paths: string[]) => void) => subscribe(IPC.event.chatPendingFeed, cb),
    setPendingFeed: (paths: string[]) => ipcRenderer.invoke(IPC.invoke.chatSetPendingFeed, paths),
    takePendingFeed: () => ipcRenderer.invoke(IPC.invoke.chatTakePendingFeed)
  },
  skills: {
    list: () => ipcRenderer.invoke(IPC.invoke.skillsList),
    setEnabled: (payload: { enabled: boolean; name: string }) => ipcRenderer.invoke(IPC.invoke.skillSetEnabled, payload)
  },
  sprite: {
    getPosition: () => ipcRenderer.invoke(IPC.invoke.spriteGetPosition),
    hide: () => ipcRenderer.invoke(IPC.invoke.spriteHide),
    moveToCursorDisplay: () => ipcRenderer.invoke(IPC.invoke.spriteMoveToCursorDisplay),
    setIgnoreMouseEvents: (payload: { forward?: boolean; ignore: boolean }) =>
      ipcRenderer.invoke(IPC.invoke.spriteSetIgnoreMouseEvents, payload),
    setPosition: (payload: { x: number; y: number }) => ipcRenderer.invoke(IPC.invoke.spriteSetPosition, payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke(IPC.invoke.toolsetsList),
    setEnabled: (payload: { enabled: boolean; id: string }) => ipcRenderer.invoke(IPC.invoke.toolsetSetEnabled, payload)
  },
  update: {
    check: () => ipcRenderer.invoke(IPC.invoke.updateCheck),
    onEvent: (cb: (payload: DesktopUpdateEvent) => void) => subscribe(IPC.event.updateEvent, cb)
  },
  writeClipboard: (text: string) => ipcRenderer.invoke(IPC.invoke.writeClipboard, text)
})
