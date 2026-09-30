import {
  type AttachmentVideoUploadPayload,
  type DesktopActivatePayload,
  type DesktopAuthBroadcast,
  type DesktopGatewayEvent,
  type DesktopGatewayRpcRequest,
  type DesktopGatewayRpcResponse,
  type DesktopGatewayState,
  type DesktopLogoutPayload,
  type DesktopPrefsHydrated,
  type DesktopRunnerStatusEvent,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  type DesktopSpritePosition,
  type DesktopSpriteScalePayload,
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  type DesktopUiThemeBroadcast,
  type DesktopUpdateEvent,
  IPC,
  type IpcEventChannel,
  type IpcEventContract,
  type IpcInvokeContract,
  type MediaSttPayload,
  type MediaTtsPayload,
  type RunnerCallRequest,
  type RunnerConfigPatch,
  type SessionHistorySnapshot,
  type SpiritAgentApiRequest,
  type SpiritAgentPrefsSet,
  type SpiritAgentSelectPathsOptions,
  type SpiritAgentUiTheme,
  type SurfaceCompanionPreference,
  type SurfacePlaybackClaim
} from '@ipc/contracts'
import { contextBridge, ipcRenderer, type IpcRendererEvent, webUtils } from 'electron'

type InvokeChannel = keyof IpcInvokeContract
type InvokePayload<C extends InvokeChannel> = Parameters<IpcInvokeContract[C]>[0]
type EventCallback<C extends IpcEventChannel> = (...payload: IpcEventContract[C]) => void

// 按契约校验通道参数与返回值；ipcRenderer.invoke 本身不带类型。
function invoke<C extends InvokeChannel>(
  channel: C,
  ...args: Parameters<IpcInvokeContract[C]>
): Promise<Awaited<ReturnType<IpcInvokeContract[C]>>> {
  return ipcRenderer.invoke(channel, ...args)
}

// Electron 32+ 移除了 File.path，真实路径只能经 webUtils.getPathForFile 拿到；解析成功即写入主进程可读白名单，不向渲染层暴露 registerUserSelectedPaths，防止 XSS 自授后外传。
contextBridge.exposeInMainWorld('spiritagentWebUtils', {
  getPathForFile: (file: File): string => {
    const filePath = webUtils.getPathForFile(file)

    if (filePath) {
      void invoke(IPC.invoke.registerUserSelectedPaths, [filePath]).catch(() => {})
    }

    return filePath
  }
})

// 订阅主进程单方向事件：listener 解构 payload 丢弃 IpcRendererEvent，返回卸载函数供 useEffect 清理。
function subscribe<C extends IpcEventChannel>(channel: C, callback: EventCallback<C>): () => void {
  const listener = (_event: IpcRendererEvent, ...payload: IpcEventContract[C]): void => {
    ;(callback as (...args: unknown[]) => void)(...payload)
  }

  ipcRenderer.on(channel, listener)

  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('spiritagent', {
  activate: (payload: DesktopActivatePayload) => invoke(IPC.invoke.authActivate, payload),
  api: (request: SpiritAgentApiRequest) => invoke(IPC.invoke.api, request),
  apiAsset: (request: InvokePayload<typeof IPC.invoke.apiAsset>) => invoke(IPC.invoke.apiAsset, request),
  apiAssetBuffer: (request: InvokePayload<typeof IPC.invoke.apiAssetBuffer>) =>
    invoke(IPC.invoke.apiAssetBuffer, request),
  sessionHistory: {
    get: (sessionId: string, authSessionId: string) => invoke(IPC.invoke.sessionHistoryGet, sessionId, authSessionId),
    save: (sessionId: string, snapshot: SessionHistorySnapshot, authSessionId: string) =>
      invoke(IPC.invoke.sessionHistorySave, sessionId, snapshot, authSessionId),
    remove: (sessionId: string, authSessionId: string) =>
      invoke(IPC.invoke.sessionHistoryRemove, sessionId, authSessionId)
  },
  getGatewayWsUrl: () => invoke(IPC.invoke.gatewayWsUrl),
  gatewayRequest: (payload: InvokePayload<typeof IPC.invoke.gatewayRequest>) =>
    invoke(IPC.invoke.gatewayRequest, payload),
  gatewayGetState: () => invoke(IPC.invoke.gatewayGetState),
  gatewayBroadcastState: (state: DesktopGatewayState) => ipcRenderer.send(IPC.send.gatewayBroadcastState, { state }),
  gatewayBroadcastEvent: (event: DesktopGatewayEvent) => ipcRenderer.send(IPC.send.gatewayBroadcastEvent, { event }),
  gatewayRpcReply: (payload: DesktopGatewayRpcResponse) => ipcRenderer.send(IPC.send.gatewayRpcReply, payload),
  onGatewayStateChanged: (cb: EventCallback<typeof IPC.event.gatewayStateChanged>) =>
    subscribe(IPC.event.gatewayStateChanged, cb),
  onGatewayEvent: (cb: EventCallback<typeof IPC.event.gatewayEvent>) => subscribe(IPC.event.gatewayEvent, cb),
  onGatewayRpcDispatch: (cb: (payload: DesktopGatewayRpcRequest) => void) =>
    subscribe(IPC.event.gatewayRpcDispatch, cb),
  getSession: () => invoke(IPC.invoke.authGetSession),
  getVersion: () => invoke(IPC.invoke.version),
  log: (payload: InvokePayload<typeof IPC.invoke.logEmit>) => invoke(IPC.invoke.logEmit, payload),
  logout: (payload: DesktopLogoutPayload) => invoke(IPC.invoke.authLogout, payload),
  media: {
    onboardingAudio: {
      read: (tag: string) => invoke(IPC.invoke.onboardingAudioRead, tag)
    },
    stt: (payload: MediaSttPayload) => invoke(IPC.invoke.mediaStt, payload),
    tts: (payload: MediaTtsPayload) => invoke(IPC.invoke.mediaTts, payload)
  },
  onAuthChanged: (cb: (payload: DesktopAuthBroadcast) => void) => subscribe(IPC.event.authChanged, cb),
  onPowerResume: (cb: () => void) => subscribe(IPC.event.powerResume, cb),
  onRunnerStatus: (cb: (payload: DesktopRunnerStatusEvent) => void) => subscribe(IPC.event.runnerStatus, cb),
  onSessionExpired: (cb: () => void) => subscribe(IPC.event.authSessionExpired, cb),
  onTrayActivate: (cb: () => void) => subscribe(IPC.event.trayActivate, cb),
  onTrayResetPosition: (cb: () => void) => subscribe(IPC.event.trayResetPosition, cb),
  onPrefsHydrated: (cb: (payload: DesktopPrefsHydrated) => void) => subscribe(IPC.event.prefsHydrated, cb),
  onUiThemeChanged: (cb: (payload: DesktopUiThemeBroadcast) => void) => subscribe(IPC.event.uiThemeChanged, cb),
  readFileDataUrl: (filePath: string) => invoke(IPC.invoke.readFileDataUrl, filePath),
  readImageForAttach: (filePath: string) => invoke(IPC.invoke.readImageForAttach, filePath),
  uploadVideoForAttach: (payload: AttachmentVideoUploadPayload) => invoke(IPC.invoke.mediaVideoUpload, payload),
  refreshSession: () => invoke(IPC.invoke.authRefresh),
  runnerCancel: (callId: string) => invoke(IPC.invoke.runnerCancel, callId),
  runnerConfig: {
    patch: (patch: RunnerConfigPatch) => invoke(IPC.invoke.runnerConfigPatch, patch),
    read: () => invoke(IPC.invoke.runnerConfigRead)
  },
  runnerGetState: () => invoke(IPC.invoke.runnerGetState),
  runnerGetTools: () => invoke(IPC.invoke.runnerGetTools),
  runnerDispatchCall: (request: RunnerCallRequest) => invoke(IPC.invoke.runnerDispatchCall, request),
  runnerInvoke: (name: string, args: Record<string, unknown>) => invoke(IPC.invoke.runnerInvoke, name, args),
  selectPaths: (options?: SpiritAgentSelectPathsOptions) => invoke(IPC.invoke.selectPaths, options),
  prefs: {
    set: (payload: SpiritAgentPrefsSet) => ipcRenderer.send(IPC.send.prefsSet, payload)
  },
  setUiTheme: (payload: SpiritAgentUiTheme) => ipcRenderer.send(IPC.send.uiTheme, payload),
  shortcuts: {
    get: () => invoke(IPC.invoke.shortcutsGet),
    onChanged: (cb: (payload: DesktopShortcutsState) => void) => subscribe(IPC.event.shortcutsChanged, cb),
    set: (payload: DesktopShortcutsSetPayload) => invoke(IPC.invoke.shortcutsSet, payload)
  },
  surface: {
    close: () => invoke(IPC.invoke.surfaceClose),
    getState: () => invoke(IPC.invoke.surfaceGetState),
    isMaximized: () => invoke(IPC.invoke.surfaceIsMaximized),
    claimPlay: (claim: SurfacePlaybackClaim) => invoke(IPC.invoke.surfaceClaimPlay, claim),
    maximize: () => invoke(IPC.invoke.surfaceMaximize),
    minimize: () => invoke(IPC.invoke.surfaceMinimize),
    onChanged: (cb: (payload: DesktopSurfaceChangedEvent) => void) => subscribe(IPC.event.surfaceChanged, cb),
    open: (payload: DesktopSurfaceOpenPayload) => invoke(IPC.invoke.surfaceOpen, payload),
    setCompanion: (preference: SurfaceCompanionPreference) => invoke(IPC.invoke.surfaceSetCompanion, preference),
    setIgnoreMouseEvents: (payload: InvokePayload<typeof IPC.invoke.surfaceSetIgnoreMouseEvents>) =>
      invoke(IPC.invoke.surfaceSetIgnoreMouseEvents, payload)
  },
  chat: {
    onPendingFeed: (cb: (paths: string[]) => void) => subscribe(IPC.event.chatPendingFeed, cb),
    setPendingFeed: (paths: string[]) => invoke(IPC.invoke.chatSetPendingFeed, paths),
    takePendingFeed: () => invoke(IPC.invoke.chatTakePendingFeed)
  },
  skills: {
    list: () => invoke(IPC.invoke.skillsList),
    setEnabled: (payload: InvokePayload<typeof IPC.invoke.skillSetEnabled>) =>
      invoke(IPC.invoke.skillSetEnabled, payload)
  },
  sprite: {
    onDefaultScaleChanged: (cb: (payload: DesktopSpriteScalePayload) => void) =>
      subscribe(IPC.event.spriteDefaultScaleChanged, cb),
    setDefaultScale: (payload: DesktopSpriteScalePayload) => ipcRenderer.send(IPC.send.spriteSetDefaultScale, payload),
    getPosition: () => invoke(IPC.invoke.spriteGetPosition),
    getWindowScene: () => invoke(IPC.invoke.spriteGetWindowScene),
    mapScreenRect: (rect: InvokePayload<typeof IPC.invoke.spriteMapScreenRect>) =>
      invoke(IPC.invoke.spriteMapScreenRect, rect),
    moveToDisplay: (point: InvokePayload<typeof IPC.invoke.spriteMoveToDisplay>) =>
      invoke(IPC.invoke.spriteMoveToDisplay, point),
    hide: () => invoke(IPC.invoke.spriteHide),
    moveToCursorDisplay: () => invoke(IPC.invoke.spriteMoveToCursorDisplay),
    setIgnoreMouseEvents: (payload: InvokePayload<typeof IPC.invoke.spriteSetIgnoreMouseEvents>) =>
      invoke(IPC.invoke.spriteSetIgnoreMouseEvents, payload),
    setPosition: (payload: DesktopSpritePosition) => invoke(IPC.invoke.spriteSetPosition, payload)
  },
  toolsets: {
    list: () => invoke(IPC.invoke.toolsetsList),
    setEnabled: (payload: InvokePayload<typeof IPC.invoke.toolsetSetEnabled>) =>
      invoke(IPC.invoke.toolsetSetEnabled, payload)
  },
  update: {
    check: () => invoke(IPC.invoke.updateCheck),
    download: () => invoke(IPC.invoke.updateDownload),
    getState: () => invoke(IPC.invoke.updateGetState),
    install: () => invoke(IPC.invoke.updateInstall),
    onEvent: (cb: (payload: DesktopUpdateEvent) => void) => subscribe(IPC.event.updateEvent, cb)
  },
  writeClipboard: (text: string) => invoke(IPC.invoke.writeClipboard, text),
  copyImage: (payload: InvokePayload<typeof IPC.invoke.copyImage>) => invoke(IPC.invoke.copyImage, payload),
  saveImage: (payload: InvokePayload<typeof IPC.invoke.saveImage>) => invoke(IPC.invoke.saveImage, payload)
})
