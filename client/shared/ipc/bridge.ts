import {
  type DesktopApiInvoke,
  IPC,
  type IpcEventContract,
  type IpcInvokeContract,
  type IpcSendContract
} from './contracts'

type EventChannel = keyof IpcEventContract
type Listener<C extends EventChannel> = (event: unknown, ...payload: IpcEventContract[C]) => void

interface PreloadIpc {
  invoke(channel: keyof IpcInvokeContract, ...args: unknown[]): Promise<unknown>
  send(channel: keyof IpcSendContract, ...args: unknown[]): void
  on<C extends EventChannel>(channel: C, listener: Listener<C>): void
  removeListener<C extends EventChannel>(channel: C, listener: Listener<C>): void
}

// 通道在 preload 装配时固定。窗口只拿到下方列出的操作，订阅回调只接收业务载荷。
function channelBindings(ipc: PreloadIpc) {
  const request =
    <C extends keyof IpcInvokeContract>(channel: C) =>
    (...args: Parameters<IpcInvokeContract[C]>) =>
      ipc.invoke(channel, ...args) as Promise<Awaited<ReturnType<IpcInvokeContract[C]>>>

  const send =
    <C extends keyof IpcSendContract>(channel: C) =>
    (...args: IpcSendContract[C]) =>
      ipc.send(channel, ...args)

  const subscribe =
    <C extends EventChannel>(channel: C) =>
    (callback: (...payload: IpcEventContract[C]) => void) => {
      const listener: Listener<C> = (_event, ...payload) => callback(...payload)
      ipc.on(channel, listener)

      return () => {
        ipc.removeListener(channel, listener)
      }
    }

  return { request, send, subscribe }
}

export function createDesktopBridge(
  ipc: PreloadIpc,
  platform: string,
  getDroppedPaths: (files: File[]) => Promise<string[]>
) {
  const { request, send, subscribe } = channelBindings(ipc)
  const addPaths = request(IPC.invoke.dockAddDropped)

  return {
    platform,
    getDroppedPaths,
    uiState: {
      get: request(IPC.invoke.uiStateGet),
      update: request(IPC.invoke.uiStateUpdate),
      ready: request(IPC.invoke.uiStateReady),
      inputBusy: request(IPC.invoke.uiStateInputBusy),
      reply: request(IPC.invoke.uiStateReply),
      onRequest: subscribe(IPC.event.uiStateRequest)
    },
    presentation: {
      setIgnoreMouseEvents: request(IPC.invoke.presentationSetIgnoreMouseEvents),
      getState: request(IPC.invoke.presentationGetState),
      requestAccessibility: request(IPC.invoke.presentationRequestAccessibility),
      setMode: request(IPC.invoke.presentationSetMode),
      setDisplay: request(IPC.invoke.presentationSetDisplay),
      reportReady: request(IPC.invoke.presentationReportReady),
      focus: request(IPC.invoke.presentationFocus),
      heartbeat: request(IPC.invoke.presentationHeartbeat),
      hostReady: request(IPC.invoke.presentationHostReady),
      setBackground: request(IPC.invoke.presentationSetBackground),
      setTrayLayout: request(IPC.invoke.presentationSetTrayLayout),
      stageActivity: request(IPC.invoke.presentationStageActivity),
      onChanged: subscribe(IPC.event.presentationChanged),
      onPointerPosition: subscribe(IPC.event.presentationPointerPosition),
      onBackgroundPlayback: subscribe(IPC.event.backgroundPlayback)
    },
    dock: {
      getState: request(IPC.invoke.dockGetState),
      catalog: request(IPC.invoke.dockCatalog),
      catalogIcons: request(IPC.invoke.dockCatalogIcons),
      addFromCatalog: request(IPC.invoke.dockAddFromCatalog),
      addFromFiles: request(IPC.invoke.dockAddFromFiles),
      addDroppedFiles: async (dropped: File[]) => addPaths(await getDroppedPaths(dropped)),
      setPreview: request(IPC.invoke.dockSetPreview),
      activate: request(IPC.invoke.dockActivate),
      activateCatalog: request(IPC.invoke.dockActivateCatalog),
      closeWindows: request(IPC.invoke.dockCloseWindows),
      pin: request(IPC.invoke.dockPin),
      reorder: request(IPC.invoke.dockReorder),
      remove: request(IPC.invoke.dockRemove),
      repairWithCatalog: request(IPC.invoke.dockRepairWithCatalog),
      repairWithFiles: request(IPC.invoke.dockRepairWithFiles),
      tasks: request(IPC.invoke.dockTasks),
      activateTask: request(IPC.invoke.dockActivateTask),
      onChanged: subscribe(IPC.event.dockChanged),
      onPreviewSelected: subscribe(IPC.event.dockPreviewSelected)
    },
    desktop: {
      accounts: request(IPC.invoke.desktopAccounts),
      switchAccount: request(IPC.invoke.desktopSwitchAccount),
      addAccount: request(IPC.invoke.desktopAddAccount),
      removeAccount: request(IPC.invoke.desktopRemoveAccount),
      quit: request(IPC.invoke.desktopQuit),
      onNavigate: subscribe(IPC.event.desktopNavigate)
    },
    gatewayPresence: request(IPC.invoke.gatewayPresence),
    gatewayGetState: request(IPC.invoke.gatewayGetState),
    voiceConnect: request(IPC.invoke.voiceConnect),
    voiceSend: request(IPC.invoke.voiceSend),
    voiceClose: request(IPC.invoke.voiceClose),
    voiceBusy: request(IPC.invoke.voiceBusy),
    onVoiceEvent: subscribe(IPC.event.voiceEvent),
    onGatewayStateChanged: subscribe(IPC.event.gatewayStateChanged),
    onGatewayEvent: subscribe(IPC.event.gatewayEvent),
    activate: request(IPC.invoke.authActivate),
    refreshSession: request(IPC.invoke.authRefresh),
    logout: request(IPC.invoke.authLogout),
    getSession: request(IPC.invoke.authGetSession),
    api: request(IPC.invoke.api) as DesktopApiInvoke,
    apiAsset: request(IPC.invoke.apiAsset),
    apiAssetBuffer: request(IPC.invoke.apiAssetBuffer),
    sessionHistory: {
      get: request(IPC.invoke.sessionHistoryGet),
      save: request(IPC.invoke.sessionHistorySave),
      remove: request(IPC.invoke.sessionHistoryRemove)
    },
    voicePlayback: {
      get: request(IPC.invoke.voicePlaybackGet),
      update: request(IPC.invoke.voicePlaybackUpdate),
      remove: request(IPC.invoke.voicePlaybackRemove),
      onChanged: subscribe(IPC.event.voicePlaybackChanged)
    },
    readFileDataUrl: request(IPC.invoke.readFileDataUrl),
    readImageForAttach: request(IPC.invoke.readImageForAttach),
    uploadVideoForAttach: request(IPC.invoke.mediaVideoUpload),
    selectPaths: request(IPC.invoke.selectPaths),
    writeClipboard: request(IPC.invoke.writeClipboard),
    copyImage: request(IPC.invoke.copyImage),
    saveImage: request(IPC.invoke.saveImage),
    log: request(IPC.invoke.logEmit),
    runnerInvoke: request(IPC.invoke.runnerInvoke),
    runnerGetState: request(IPC.invoke.runnerGetState),
    setUiTheme: send(IPC.send.uiTheme),
    prefs: {
      applyServerVoice: request(IPC.invoke.prefsApplyServerVoice),
      set: send(IPC.send.prefsSet)
    },
    shortcuts: {
      get: request(IPC.invoke.shortcutsGet),
      onChanged: subscribe(IPC.event.shortcutsChanged),
      set: request(IPC.invoke.shortcutsSet)
    },
    surface: {
      open: request(IPC.invoke.surfaceOpen),
      close: request(IPC.invoke.surfaceClose),
      isMaximized: request(IPC.invoke.surfaceIsMaximized),
      setCallLayout: request(IPC.invoke.surfaceCallLayout),
      maximize: request(IPC.invoke.surfaceMaximize),
      minimize: request(IPC.invoke.surfaceMinimize),
      getState: request(IPC.invoke.surfaceGetState),
      setCompanion: request(IPC.invoke.surfaceSetCompanion),
      companionVisibility: request(IPC.invoke.surfaceCompanionVisibility),
      setIgnoreMouseEvents: request(IPC.invoke.surfaceSetIgnoreMouseEvents),
      onChanged: subscribe(IPC.event.surfaceChanged),
      onNavigate: subscribe(IPC.event.surfaceNavigate)
    },
    chat: {
      onPendingFeed: subscribe(IPC.event.chatPendingFeed),
      setPendingFeed: request(IPC.invoke.chatSetPendingFeed),
      takePendingFeed: request(IPC.invoke.chatTakePendingFeed)
    },
    runnerConfig: {
      read: request(IPC.invoke.runnerConfigRead),
      patch: request(IPC.invoke.runnerConfigPatch)
    },
    skills: {
      list: request(IPC.invoke.skillsList),
      setEnabled: request(IPC.invoke.skillSetEnabled)
    },
    toolsets: {
      list: request(IPC.invoke.toolsetsList),
      setEnabled: request(IPC.invoke.toolsetSetEnabled)
    },
    media: {
      prepareOutput: request(IPC.invoke.mediaPrepareOutput),
      uploadAudio: request(IPC.invoke.mediaAudioUpload),
      uploadVoiceSample: request(IPC.invoke.mediaVoiceSampleUpload),
      tts: request(IPC.invoke.mediaTts)
    },
    sprite: {
      onDefaultScaleChanged: subscribe(IPC.event.spriteDefaultScaleChanged),
      setDefaultScale: send(IPC.send.spriteSetDefaultScale),
      hide: request(IPC.invoke.spriteHide),
      setIgnoreMouseEvents: request(IPC.invoke.spriteSetIgnoreMouseEvents),
      getPosition: request(IPC.invoke.spriteGetPosition),
      setPosition: request(IPC.invoke.spriteSetPosition),
      moveToCursorDisplay: request(IPC.invoke.spriteMoveToCursorDisplay)
    },
    onSessionExpired: subscribe(IPC.event.authSessionExpired),
    onAuthChanged: subscribe(IPC.event.authChanged),
    onRunnerStatus: subscribe(IPC.event.runnerStatus),
    onTrayActivate: subscribe(IPC.event.trayActivate),
    onTrayResetPosition: subscribe(IPC.event.trayResetPosition),
    onUiThemeChanged: subscribe(IPC.event.uiThemeChanged),
    onPrefsHydrated: subscribe(IPC.event.prefsHydrated),
    getVersion: request(IPC.invoke.version)
  }
}

export function createBackgroundBridge(ipc: PreloadIpc) {
  const { request, subscribe } = channelBindings(ipc)

  return {
    ready: request(IPC.invoke.backgroundReady),
    acknowledge: request(IPC.invoke.backgroundAcknowledge),
    onMedia: subscribe(IPC.event.backgroundMedia)
  }
}

export type DesktopBridge = ReturnType<typeof createDesktopBridge>
export type DesktopBackgroundBridge = ReturnType<typeof createBackgroundBridge>
