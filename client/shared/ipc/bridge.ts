import type { DesktopApiInvoke, IpcEventContract, IpcInvokeContract, IpcSendContract } from './contracts'

type AsyncIpc<T extends (...args: never[]) => unknown> = (...args: Parameters<T>) => Promise<Awaited<ReturnType<T>>>

type EventSubscription<K extends keyof IpcEventContract> = IpcEventContract[K] extends [infer P]
  ? (callback: (payload: P) => void) => () => void
  : (callback: () => void) => () => void

export interface DesktopBridge {
  platform: string
  uiState: {
    get: AsyncIpc<IpcInvokeContract['spiritagent:ui-state:get']>
    update: AsyncIpc<IpcInvokeContract['spiritagent:ui-state:update']>
    ready: AsyncIpc<IpcInvokeContract['spiritagent:ui-state:ready']>
    inputBusy: AsyncIpc<IpcInvokeContract['spiritagent:ui-state:input-busy']>
    reply: AsyncIpc<IpcInvokeContract['spiritagent:ui-state:reply']>
    onRequest: EventSubscription<'spiritagent:ui-state:request'>
  }
  presentation: {
    companionActivity: AsyncIpc<IpcInvokeContract['spiritagent:presentation:companion-activity']>
    setIgnoreMouseEvents: AsyncIpc<IpcInvokeContract['spiritagent:presentation:set-ignore-mouse-events']>
    getState: AsyncIpc<IpcInvokeContract['spiritagent:presentation:get-state']>
    requestAccessibility: AsyncIpc<IpcInvokeContract['spiritagent:presentation:request-accessibility']>
    setMode: AsyncIpc<IpcInvokeContract['spiritagent:presentation:set-mode']>
    setDisplay: AsyncIpc<IpcInvokeContract['spiritagent:presentation:set-display']>
    reportReady: AsyncIpc<IpcInvokeContract['spiritagent:presentation:report-ready']>
    focus: AsyncIpc<IpcInvokeContract['spiritagent:presentation:focus']>
    heartbeat: AsyncIpc<IpcInvokeContract['spiritagent:presentation:heartbeat']>
    hostReady: AsyncIpc<IpcInvokeContract['spiritagent:presentation:host-ready']>
    setBackground: AsyncIpc<IpcInvokeContract['spiritagent:presentation:set-background']>
    setTrayLayout: AsyncIpc<IpcInvokeContract['spiritagent:presentation:set-tray-layout']>
    stageActivity: AsyncIpc<IpcInvokeContract['spiritagent:presentation:stage-activity']>
    onChanged: EventSubscription<'spiritagent:presentation:changed'>
    onPointerPosition: EventSubscription<'spiritagent:presentation:pointer-position'>
    onBackgroundPlayback: EventSubscription<'spiritagent:presentation:background-playback'>
  }
  dock: {
    getState: AsyncIpc<IpcInvokeContract['spiritagent:dock:get-state']>
    catalog: AsyncIpc<IpcInvokeContract['spiritagent:dock:catalog']>
    catalogIcons: AsyncIpc<IpcInvokeContract['spiritagent:dock:catalog-icons']>
    addFromCatalog: AsyncIpc<IpcInvokeContract['spiritagent:dock:add-from-catalog']>
    addFromFiles: AsyncIpc<IpcInvokeContract['spiritagent:dock:add-from-files']>
    addDroppedFiles: (files: File[]) => Promise<Awaited<ReturnType<IpcInvokeContract['spiritagent:dock:add-dropped']>>>
    activate: AsyncIpc<IpcInvokeContract['spiritagent:dock:activate']>
    activateCatalog: AsyncIpc<IpcInvokeContract['spiritagent:dock:activate-catalog']>
    closeWindows: AsyncIpc<IpcInvokeContract['spiritagent:dock:close-windows']>
    pin: AsyncIpc<IpcInvokeContract['spiritagent:dock:pin']>
    reorder: AsyncIpc<IpcInvokeContract['spiritagent:dock:reorder']>
    remove: AsyncIpc<IpcInvokeContract['spiritagent:dock:remove']>
    repairWithCatalog: AsyncIpc<IpcInvokeContract['spiritagent:dock:repair-with-catalog']>
    repairWithFiles: AsyncIpc<IpcInvokeContract['spiritagent:dock:repair-with-files']>
    tasks: AsyncIpc<IpcInvokeContract['spiritagent:dock:tasks']>
    activateTask: AsyncIpc<IpcInvokeContract['spiritagent:dock:activate-task']>
    onChanged: EventSubscription<'spiritagent:dock:changed'>
  }
  desktop: {
    accounts: AsyncIpc<IpcInvokeContract['spiritagent:desktop:accounts']>
    switchAccount: AsyncIpc<IpcInvokeContract['spiritagent:desktop:switch-account']>
    addAccount: AsyncIpc<IpcInvokeContract['spiritagent:desktop:add-account']>
    removeAccount: AsyncIpc<IpcInvokeContract['spiritagent:desktop:remove-account']>
    quit: AsyncIpc<IpcInvokeContract['spiritagent:desktop:quit']>
    onNavigate: EventSubscription<'spiritagent:desktop:navigate'>
  }
  gatewayPresence: AsyncIpc<IpcInvokeContract['spiritagent:gateway:presence']>
  gatewayGetState: AsyncIpc<IpcInvokeContract['spiritagent:gateway:get-state']>
  onGatewayStateChanged: EventSubscription<'spiritagent:gateway:state-changed'>
  onGatewayEvent: EventSubscription<'spiritagent:gateway:event'>
  activate: AsyncIpc<IpcInvokeContract['spiritagent:auth:activate']>
  refreshSession: AsyncIpc<IpcInvokeContract['spiritagent:auth:refresh']>
  logout: AsyncIpc<IpcInvokeContract['spiritagent:auth:logout']>
  getSession: AsyncIpc<IpcInvokeContract['spiritagent:auth:get-session']>
  api: DesktopApiInvoke
  /** 把后端服务的二进制资产以 data URL 的形式取回；仅查本地缓存时未命中返回 null。 */
  apiAsset: AsyncIpc<IpcInvokeContract['spiritagent:api:asset']>
  /** 把后端服务的二进制资产以原始字节取回——用于视频片段等大体积负载,不能接受 base64 膨胀。支持通过 contentHash 做磁盘缓存。 */
  apiAssetBuffer: AsyncIpc<IpcInvokeContract['spiritagent:api:asset-buffer']>
  sessionHistory: {
    get: AsyncIpc<IpcInvokeContract['spiritagent:session-history:get']>
    save: AsyncIpc<IpcInvokeContract['spiritagent:session-history:save']>
    remove: AsyncIpc<IpcInvokeContract['spiritagent:session-history:remove']>
  }
  voicePlayback: {
    get: AsyncIpc<IpcInvokeContract['spiritagent:voice-playback:get']>
    update: AsyncIpc<IpcInvokeContract['spiritagent:voice-playback:update']>
    remove: AsyncIpc<IpcInvokeContract['spiritagent:voice-playback:remove']>
    onChanged: EventSubscription<'spiritagent:voice-playback:changed'>
  }
  readFileDataUrl: AsyncIpc<IpcInvokeContract['spiritagent:readFileDataUrl']>
  /** 聊天图片附件读取：超限降采样重编码，产出可直接发送的 data URL。 */
  readImageForAttach: AsyncIpc<IpcInvokeContract['spiritagent:readImageForAttach']>
  /** 聊天视频附件上传：主进程读文件经后端 /api/media/videos 换取会话级附件 URL。 */
  uploadVideoForAttach: AsyncIpc<IpcInvokeContract['spiritagent:media:video-upload']>
  /** 桌景动作外部视频上传：主进程读取用户选择的原始文件并以 multipart 发送。 */
  uploadDesktopVideo: AsyncIpc<IpcInvokeContract['spiritagent:media:desktop-video-upload']>
  selectPaths: AsyncIpc<IpcInvokeContract['spiritagent:selectPaths']>
  writeClipboard: AsyncIpc<IpcInvokeContract['spiritagent:writeClipboard']>
  copyImage: AsyncIpc<IpcInvokeContract['spiritagent:copyImage']>
  saveImage: AsyncIpc<IpcInvokeContract['spiritagent:saveImage']>
  log: AsyncIpc<IpcInvokeContract['spiritagent:log:emit']>
  runnerInvoke: AsyncIpc<IpcInvokeContract['spiritagent:runner:invoke']>
  runnerGetState: AsyncIpc<IpcInvokeContract['spiritagent:runner:get-state']>
  runnerGetTools: AsyncIpc<IpcInvokeContract['spiritagent:runner:get-tools']>
  setUiTheme: (payload: IpcSendContract['spiritagent:ui-theme'][0]) => void
  prefs: {
    applyServerVoice: AsyncIpc<IpcInvokeContract['spiritagent:prefs:apply-server-voice']>
    set: (payload: IpcSendContract['spiritagent:prefs:set'][0]) => void
  }
  shortcuts: {
    get: AsyncIpc<IpcInvokeContract['spiritagent:shortcuts:get']>
    onChanged: EventSubscription<'spiritagent:shortcuts:changed'>
    set: AsyncIpc<IpcInvokeContract['spiritagent:shortcuts:set']>
  }
  surface: {
    open: AsyncIpc<IpcInvokeContract['spiritagent:surface:open']>
    close: AsyncIpc<IpcInvokeContract['spiritagent:surface:close']>
    isMaximized: AsyncIpc<IpcInvokeContract['spiritagent:surface:is-maximized']>
    maximize: AsyncIpc<IpcInvokeContract['spiritagent:surface:maximize']>
    minimize: AsyncIpc<IpcInvokeContract['spiritagent:surface:minimize']>
    getState: AsyncIpc<IpcInvokeContract['spiritagent:surface:get-state']>
    setCompanion: AsyncIpc<IpcInvokeContract['spiritagent:surface:set-companion']>
    setIgnoreMouseEvents: AsyncIpc<IpcInvokeContract['spiritagent:surface:set-ignore-mouse-events']>
    claimPlay: AsyncIpc<IpcInvokeContract['spiritagent:surface:claim-play']>
    onChanged: EventSubscription<'spiritagent:surface:changed'>
    onNavigate: EventSubscription<'spiritagent:surface:navigate'>
  }
  chat: {
    onPendingFeed: EventSubscription<'spiritagent:chat:pending-feed'>
    setPendingFeed: AsyncIpc<IpcInvokeContract['spiritagent:chat:set-pending-feed']>
    takePendingFeed: AsyncIpc<IpcInvokeContract['spiritagent:chat:take-pending-feed']>
  }
  runnerConfig: {
    read: AsyncIpc<IpcInvokeContract['spiritagent:runner-config:read']>
    patch: AsyncIpc<IpcInvokeContract['spiritagent:runner-config:patch']>
  }
  skills: {
    list: AsyncIpc<IpcInvokeContract['spiritagent:skills:list']>
    setEnabled: AsyncIpc<IpcInvokeContract['spiritagent:skill:set-enabled']>
  }
  toolsets: {
    list: AsyncIpc<IpcInvokeContract['spiritagent:toolsets:list']>
    setEnabled: AsyncIpc<IpcInvokeContract['spiritagent:toolset:set-enabled']>
  }
  media: {
    stt: AsyncIpc<IpcInvokeContract['spiritagent:media:stt']>
    uploadAudio: AsyncIpc<IpcInvokeContract['spiritagent:media:audio-upload']>
    uploadVoiceSample: AsyncIpc<IpcInvokeContract['spiritagent:media:voice-sample-upload']>
    tts: AsyncIpc<IpcInvokeContract['spiritagent:media:tts']>
    onboardingAudio: {
      read: AsyncIpc<IpcInvokeContract['spiritagent:onboardingAudio:read']>
    }
  }
  sprite: {
    onDefaultScaleChanged: EventSubscription<'spiritagent:sprite:default-scale-changed'>
    setDefaultScale: (payload: IpcSendContract['spiritagent:sprite:set-default-scale'][0]) => void
    hide: AsyncIpc<IpcInvokeContract['spiritagent:sprite:hide']>
    setIgnoreMouseEvents: AsyncIpc<IpcInvokeContract['spiritagent:sprite:set-ignore-mouse-events']>
    getPosition: AsyncIpc<IpcInvokeContract['spiritagent:sprite:get-position']>
    getWindowScene: AsyncIpc<IpcInvokeContract['spiritagent:sprite:get-window-scene']>
    moveToDisplay: AsyncIpc<IpcInvokeContract['spiritagent:sprite:move-to-display']>
    setPosition: AsyncIpc<IpcInvokeContract['spiritagent:sprite:set-position']>
    moveToCursorDisplay: AsyncIpc<IpcInvokeContract['spiritagent:sprite:move-to-cursor-display']>
  }
  onPowerResume: EventSubscription<'spiritagent:power-resume'>
  onSessionExpired: EventSubscription<'spiritagent:auth:session-expired'>
  onAuthChanged: EventSubscription<'spiritagent:auth:changed'>
  onRunnerStatus: EventSubscription<'spiritagent:runner:status'>
  onTrayActivate: EventSubscription<'spiritagent:tray:activate'>
  onTrayResetPosition: EventSubscription<'spiritagent:tray:reset-position'>
  onUiThemeChanged: EventSubscription<'spiritagent:ui-theme-changed'>
  onPrefsHydrated: EventSubscription<'spiritagent:prefs-hydrated'>
  getVersion: AsyncIpc<IpcInvokeContract['spiritagent:version']>
  update: {
    check: AsyncIpc<IpcInvokeContract['spiritagent:update:check']>
    download: AsyncIpc<IpcInvokeContract['spiritagent:update:download']>
    getState: AsyncIpc<IpcInvokeContract['spiritagent:update:get-state']>
    install: AsyncIpc<IpcInvokeContract['spiritagent:update:install']>
    onEvent: EventSubscription<'spiritagent:update-event'>
  }
}

export interface DesktopBackgroundBridge {
  ready: () => Promise<void>
  onMedia: EventSubscription<'spiritagent:background:media'>
  acknowledge: AsyncIpc<IpcInvokeContract['spiritagent:background:acknowledge']>
}

export interface DesktopFileBridge {
  getPathForFile: (file: File) => string
}
