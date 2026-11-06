import type {
  AppNavigationTarget,
  DesktopAccount,
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopBackgroundRequest,
  DesktopTrayLayout,
  DockCatalog,
  DockState,
  DockTask,
  PresentationMode,
  PresentationState,
  StageActivity
} from './desktop-presentation'

export * from './contracts/account'
export * from './contracts/desktop'
export * from './contracts/media'
export * from './contracts/runner'

export * from './contracts/session'
export * from './contracts/ui-state'
export * from './contracts/update'
export {
  APP_PAGES,
  COMPANION_SECTIONS,
  composeNavigation,
  CREATION_SECTIONS,
  DEFAULT_APP_LOCATION,
  DEFAULT_NAVIGATION,
  DEFAULT_SECTION,
  DEFAULT_STAGE_LAYOUT,
  LIFE_SECTIONS,
  navigationHash,
  normalizeAppLocation,
  normalizeAppNavigation,
  normalizePageFocus,
  parseNavigationHash,
  SETTINGS_SECTIONS,
  SETTINGS_TABS
} from './desktop-presentation'
export type {
  AppLocation,
  AppNavigationTarget,
  AppPage,
  CompanionSection,
  CreationSection,
  DesktopAccount,
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopBackgroundRequest,
  DesktopMediaBytes,
  DesktopMediaReference,
  DesktopStageLayer,
  DesktopStageLayout,
  DesktopTrayLayout,
  DockCatalog,
  DockCatalogItem,
  DockCatalogSource,
  DockCatalogSourceKey,
  DockEntry,
  DockState,
  DockTask,
  DockWindow,
  LifeSection,
  PageFocus,
  PresentationDisplay,
  PresentationMode,
  PresentationState,
  SettingsSection,
  SettingsTab,
  StageActivity
} from './desktop-presentation'
export type {
  ActionComposerPreview,
  ActionComposerSnapshot,
  ActionsUiSnapshot,
  AppearanceUiSnapshot,
  CompanionEditorUiSnapshot,
  DiaryUiSnapshot,
  LifeUiSnapshot,
  SceneUiSnapshot,
  StudioUiSnapshot,
  VisualsUiSnapshot,
  WardrobeDesignMessage,
  WardrobeDesignSessionSnapshot,
  WardrobeDesignUiSnapshot
} from './ui-state'
export {
  isActionsUiSnapshot,
  isAppearanceUiSnapshot,
  isCompanionEditorUiSnapshot,
  isDiaryUiSnapshot,
  isLifeUiSnapshot,
  isSceneUiSnapshot,
  isVisualsUiSnapshot,
  isWardrobeDesignUiSnapshot
} from './ui-state'

import type {
  DesktopActivatePayload,
  DesktopApiInvoke,
  DesktopAuthBroadcast,
  DesktopAuthSnapshot,
  SpiritAgentSelectPathsOptions
} from './contracts/account'
import type {
  DesktopPrefsHydrated,
  DesktopServerVoiceSelection,
  DesktopShortcutsSetPayload,
  DesktopShortcutsState,
  DesktopSpritePosition,
  DesktopSpriteRestPosition,
  DesktopSpriteScalePayload,
  DesktopSurfaceChangedEvent,
  DesktopUiThemeBroadcast,
  DesktopVersionInfo,
  SpiritAgentPrefsSet,
  SpiritAgentUiTheme,
  SurfaceCompanionPreference
} from './contracts/desktop'
import type {
  AttachmentVideoUploadPayload,
  AttachmentVideoUploadResult,
  AudioUploadPayload,
  AudioUploadResult,
  MediaTtsPayload,
  VoicePlaybackChanged,
  VoicePlaybackRemoval,
  VoicePlaybackScope,
  VoicePlaybackSnapshot,
  VoicePlaybackUpdate,
  VoiceSampleUploadPayload,
  VoiceSampleUploadResult
} from './contracts/media'
import type {
  DesktopRunnerState,
  DesktopRunnerStatusEvent,
  RunnerConfigPatch,
  SkillItem,
  ToolsetItem
} from './contracts/runner'
import type {
  DesktopApiResult,
  DesktopGatewayEvent,
  DesktopGatewaySnapshot,
  DesktopPresenceRequest,
  SessionHistorySnapshot
} from './contracts/session'
import type {
  DesktopSurfaceOpenPayload,
  MainUiStateSnapshot,
  MainUiStateUpdate,
  UiInputBusyState,
  UiStateReply,
  UiStateRequest
} from './contracts/ui-state'
import type { DesktopUpdateEvent } from './contracts/update'

// 1. 请求-响应（渲染进程 -> 主进程，通过 ipcRenderer.invoke / ipcMain.handle）
export interface IpcInvokeContract {
  'spiritagent:presentation:set-ignore-mouse-events': (payload: { ignore: boolean; forward?: boolean }) => void
  'spiritagent:presentation:get-state': () => Promise<PresentationState>
  'spiritagent:presentation:request-accessibility': () => Promise<PresentationState>
  'spiritagent:presentation:set-mode': (mode: PresentationMode) => Promise<PresentationState>
  'spiritagent:presentation:set-display': (id: number) => Promise<PresentationState>
  'spiritagent:presentation:report-ready': () => void
  'spiritagent:presentation:focus': (epoch: number) => Promise<boolean>
  'spiritagent:presentation:heartbeat': () => void
  'spiritagent:presentation:host-ready': () => Promise<void>
  'spiritagent:presentation:set-background': (background: DesktopBackgroundRequest) => Promise<void>
  'spiritagent:presentation:set-tray-layout': (payload: DesktopTrayLayout) => Promise<void>
  'spiritagent:presentation:stage-activity': (activity: StageActivity) => void
  'spiritagent:dock:get-state': () => Promise<DockState>
  'spiritagent:dock:catalog': (force?: boolean, revision?: number) => Promise<DockCatalog>
  'spiritagent:dock:catalog-icons': (ids: string[]) => Promise<Record<string, string | null>>
  'spiritagent:dock:add-from-catalog': (ids: string[]) => Promise<DockState>
  'spiritagent:dock:add-from-files': () => Promise<DockState>
  'spiritagent:dock:add-dropped': (paths: string[]) => Promise<DockState>
  'spiritagent:dock:activate': (id: string, windowId?: string) => Promise<void>
  'spiritagent:dock:activate-catalog': (catalogId: string) => Promise<void>
  'spiritagent:dock:close-windows': (id: string, windowIds: string[]) => Promise<void>
  'spiritagent:dock:pin': (runningId: string, beforeEntryId?: string) => Promise<DockState>
  'spiritagent:dock:reorder': (ids: string[]) => Promise<DockState>
  'spiritagent:dock:remove': (id: string) => Promise<DockState>
  'spiritagent:dock:repair-with-catalog': (entryId: string, catalogId: string) => Promise<DockState>
  'spiritagent:dock:repair-with-files': (entryId: string) => Promise<DockState>
  'spiritagent:dock:tasks': (entryId: string) => Promise<DockTask[]>
  'spiritagent:dock:activate-task': (entryId: string, taskId: string) => Promise<void>
  'spiritagent:desktop:accounts': () => Promise<DesktopAccount[]>
  'spiritagent:desktop:switch-account': (id: string) => Promise<void>
  'spiritagent:desktop:add-account': () => Promise<void>
  'spiritagent:desktop:remove-account': (id: string) => Promise<void>
  'spiritagent:desktop:quit': () => void
  'spiritagent:background:ready': () => void
  'spiritagent:background:acknowledge': (playback: DesktopBackgroundPlayback) => void

  // 连接与启动
  'spiritagent:gateway:presence': (request: DesktopPresenceRequest) => Promise<void>
  'spiritagent:gateway:get-state': (authSessionId: string) => DesktopGatewaySnapshot | Promise<DesktopGatewaySnapshot>

  // 鉴权
  'spiritagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:refresh': () => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:logout': (
    sessionId: string
  ) =>
    | { backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }
    | Promise<{ backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }>
  'spiritagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>

  // 唯一主窗口
  'spiritagent:surface:open': (payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  'spiritagent:surface:close': () => Promise<void> | void
  'spiritagent:surface:minimize': () => Promise<void> | void
  'spiritagent:surface:maximize': () => Promise<void> | void
  'spiritagent:surface:is-maximized': () => Promise<boolean> | boolean
  'spiritagent:surface:get-state': () => DesktopSurfaceChangedEvent | Promise<DesktopSurfaceChangedEvent>
  'spiritagent:surface:set-companion': (
    preference: SurfaceCompanionPreference
  ) => DesktopSurfaceChangedEvent | Promise<DesktopSurfaceChangedEvent>
  'spiritagent:surface:companion-visibility': (payload: {
    authSessionId: string
    visible: boolean
  }) => DesktopSurfaceChangedEvent
  'spiritagent:surface:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void

  'spiritagent:ui-state:get': (authSessionId: string) => MainUiStateSnapshot | null
  'spiritagent:ui-state:update': (payload: MainUiStateUpdate) => void
  'spiritagent:ui-state:ready': (authSessionId: string, revision: number) => boolean
  'spiritagent:ui-state:input-busy': (authSessionId: string, state: UiInputBusyState) => void
  'spiritagent:ui-state:reply': (reply: UiStateReply) => void

  // 后端 API 代理
  'spiritagent:api': DesktopApiInvoke
  'spiritagent:api:asset': (request: {
    preferCache?: boolean
    cacheOnly?: boolean
    url: string
  }) => Promise<string | null> | string | null
  'spiritagent:api:asset-buffer': (request: { preferCache?: boolean; url: string }) => Promise<Uint8Array> | Uint8Array

  // 会话历史本地缓存：请求携带发起时的鉴权会话，主进程核对后按账户隔离。
  'spiritagent:session-history:get': (
    sessionId: string,
    authSessionId: string
  ) => SessionHistorySnapshot | null | Promise<SessionHistorySnapshot | null>
  'spiritagent:session-history:save': (
    sessionId: string,
    snapshot: SessionHistorySnapshot,
    authSessionId: string
  ) => Promise<void> | void
  'spiritagent:session-history:remove': (sessionId: string, authSessionId: string) => Promise<void> | void

  'spiritagent:voice-playback:get': (scope: VoicePlaybackScope) => Promise<VoicePlaybackSnapshot | null>
  'spiritagent:voice-playback:update': (update: VoicePlaybackUpdate) => Promise<VoicePlaybackSnapshot | null>
  'spiritagent:voice-playback:remove': (removal: VoicePlaybackRemoval) => Promise<VoicePlaybackSnapshot | null>

  // 文件 / 剪贴板 / 日志
  'spiritagent:readFileDataUrl': (filePath: string) => Promise<string> | string
  'spiritagent:readImageForAttach': (filePath: string) => Promise<string> | string
  'spiritagent:registerUserSelectedPaths': (paths: string[]) => Promise<void> | void
  /** 精灵窗投喂的混合文件路径信箱，由当前合资格对话视图取走。 */
  'spiritagent:chat:set-pending-feed': (paths: string[], authSessionId: string) => Promise<void> | void
  'spiritagent:chat:take-pending-feed': (authSessionId: string) => Promise<string[]> | string[]
  'spiritagent:selectPaths': (options?: SpiritAgentSelectPathsOptions) => Promise<string[]> | string[]
  'spiritagent:writeClipboard': (text: string) => boolean | Promise<boolean>
  /** 把 data: 或用户已选 file: 图片写入系统剪贴板。 */
  'spiritagent:copyImage': (payload: { url: string }) => boolean | Promise<boolean>
  /** 将 data:/file:（须用户已选）图片另存到用户选择的路径；取消时返回 false。 */
  'spiritagent:saveImage': (payload: { defaultName?: string; url: string }) => boolean | Promise<boolean>
  'spiritagent:log:emit': (payload: {
    args: unknown[]
    level: 'error' | 'info' | 'warn'
    scope: string
  }) => Promise<void> | void
  'spiritagent:version': () => DesktopVersionInfo | Promise<DesktopVersionInfo>

  // Runner
  'spiritagent:runner:invoke': (name: string, args: Record<string, unknown>) => Promise<unknown> | unknown
  'spiritagent:runner:get-state': () => DesktopRunnerState | Promise<DesktopRunnerState>
  'spiritagent:runner-config:read': () =>
    | { config: Record<string, unknown>; ok: true }
    | { error: string; ok: false }
    | Promise<{ config: Record<string, unknown>; ok: true } | { error: string; ok: false }>
  'spiritagent:runner-config:patch': (
    patch: RunnerConfigPatch
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>

  // Skills 与工具集
  'spiritagent:skills:list': () =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'spiritagent:skill:set-enabled': (payload: {
    enabled: boolean
    name: string
  }) =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'spiritagent:toolsets:list': () =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>
  'spiritagent:toolset:set-enabled': (payload: {
    enabled: boolean
    id: string
  }) =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>

  // 媒体
  'spiritagent:media:prepare-output': () => Promise<void>
  'spiritagent:media:audio-upload': (payload: AudioUploadPayload) => Promise<DesktopApiResult<AudioUploadResult>>
  'spiritagent:media:voice-sample-upload': (
    payload: VoiceSampleUploadPayload
  ) => Promise<DesktopApiResult<VoiceSampleUploadResult>>
  'spiritagent:media:tts': (
    payload: MediaTtsPayload
  ) => Promise<DesktopApiResult<{ dataUrl: string; mimeType: string }>>
  'spiritagent:media:video-upload': (
    payload: AttachmentVideoUploadPayload
  ) => Promise<DesktopApiResult<AttachmentVideoUploadResult>>
  'spiritagent:onboardingAudio:read': (
    tag: string
  ) =>
    | { bytes: number; dataUrl: string; mimeType: string; tag: string }
    | Promise<{ bytes: number; dataUrl: string; mimeType: string; tag: string }>

  // 偏好与快捷键
  'spiritagent:prefs:apply-server-voice': (payload: DesktopServerVoiceSelection) => Promise<boolean>
  'spiritagent:shortcuts:get': () => DesktopShortcutsState | Promise<DesktopShortcutsState>
  'spiritagent:shortcuts:set': (
    payload: DesktopShortcutsSetPayload
  ) => DesktopShortcutsState | Promise<DesktopShortcutsState>

  // 更新
  'spiritagent:update:check': () => Promise<void> | void
  'spiritagent:update:download': () => Promise<void> | void
  /** 仅主窗口或交互桌面可调用，且须处于 downloaded 状态。 */
  'spiritagent:update:install': () => Promise<void> | void
  'spiritagent:update:get-state': () => DesktopUpdateEvent | null | Promise<DesktopUpdateEvent | null>

  // 精灵窗口
  'spiritagent:sprite:hide': () => Promise<void> | void
  'spiritagent:sprite:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void
  'spiritagent:sprite:get-position': () => null | DesktopSpriteRestPosition | Promise<null | DesktopSpriteRestPosition>
  'spiritagent:sprite:set-position': (payload: DesktopSpritePosition) => Promise<void> | void
  'spiritagent:sprite:move-to-cursor-display': () =>
    | null
    | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }
    | Promise<null | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }>
}

// 2. 主进程向渲染进程推送事件（通过 webContents.send / ipcRenderer.on）
export interface IpcEventContract {
  'spiritagent:presentation:changed': [payload: PresentationState]
  'spiritagent:presentation:pointer-position': [payload: { x: number; y: number }]
  'spiritagent:dock:changed': [payload: DockState]
  'spiritagent:desktop:navigate': [payload: AppNavigationTarget]
  'spiritagent:background:media': [payload: DesktopBackground]
  'spiritagent:presentation:background-playback': [payload: DesktopBackgroundPlayback]

  'spiritagent:voice-playback:changed': [payload: VoicePlaybackChanged]
  'spiritagent:auth:changed': [payload: DesktopAuthBroadcast]
  'spiritagent:auth:session-expired': [sessionId: string]
  'spiritagent:prefs-hydrated': [payload: DesktopPrefsHydrated]
  'spiritagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'spiritagent:shortcuts:changed': [payload: DesktopShortcutsState]
  'spiritagent:surface:changed': [payload: DesktopSurfaceChangedEvent]
  'spiritagent:surface:navigate': [payload: AppNavigationTarget]
  'spiritagent:ui-state:request': [payload: UiStateRequest]
  'spiritagent:sprite:default-scale-changed': [payload: DesktopSpriteScalePayload]
  'spiritagent:chat:pending-feed': [payload: string[]]
  'spiritagent:tray:activate': []
  'spiritagent:tray:reset-position': []
  'spiritagent:ui-theme-changed': [payload: DesktopUiThemeBroadcast]
  'spiritagent:update-event': [payload: DesktopUpdateEvent]
  'spiritagent:gateway:state-changed': [payload: DesktopGatewaySnapshot & { authSessionId: string }]
  'spiritagent:gateway:event': [payload: { event: DesktopGatewayEvent; authSessionId: string }]
}

// 3. 渲染进程向主进程单向发送消息（通过 ipcRenderer.send / ipcMain.on）
export interface IpcSendContract {
  'spiritagent:prefs:set': [payload: SpiritAgentPrefsSet]
  'spiritagent:ui-theme': [payload: SpiritAgentUiTheme]
  'spiritagent:sprite:set-default-scale': [payload: DesktopSpriteScalePayload]
}

type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
type IpcSendChannel = keyof IpcSendContract

// 运行时 channel 常量。用扁平键(camelCase)避免 `Record<string, Record<string, ...>>` 守卫无法适配混合扁平/嵌套 channel 名的结构问题。每个叶子字符串都必须是对应契约接口的合法 key,任何拼写错误立即在 `satisfies` 检查处报错。在 main + preload 中以 `IPC.invoke.authActivate` 等方式使用,完全消除字面量字符串。
export const IPC = {
  invoke: {
    presentationStageActivity: 'spiritagent:presentation:stage-activity',
    presentationSetIgnoreMouseEvents: 'spiritagent:presentation:set-ignore-mouse-events',
    presentationGetState: 'spiritagent:presentation:get-state',
    presentationRequestAccessibility: 'spiritagent:presentation:request-accessibility',
    presentationSetMode: 'spiritagent:presentation:set-mode',
    presentationSetDisplay: 'spiritagent:presentation:set-display',
    presentationReportReady: 'spiritagent:presentation:report-ready',
    presentationFocus: 'spiritagent:presentation:focus',
    presentationHeartbeat: 'spiritagent:presentation:heartbeat',
    presentationHostReady: 'spiritagent:presentation:host-ready',
    presentationSetBackground: 'spiritagent:presentation:set-background',
    presentationSetTrayLayout: 'spiritagent:presentation:set-tray-layout',
    dockGetState: 'spiritagent:dock:get-state',
    dockCatalog: 'spiritagent:dock:catalog',
    dockCatalogIcons: 'spiritagent:dock:catalog-icons',
    dockAddFromCatalog: 'spiritagent:dock:add-from-catalog',
    dockAddFromFiles: 'spiritagent:dock:add-from-files',
    dockAddDropped: 'spiritagent:dock:add-dropped',
    dockActivate: 'spiritagent:dock:activate',
    dockActivateCatalog: 'spiritagent:dock:activate-catalog',
    dockCloseWindows: 'spiritagent:dock:close-windows',
    dockPin: 'spiritagent:dock:pin',
    dockReorder: 'spiritagent:dock:reorder',
    dockRemove: 'spiritagent:dock:remove',
    dockRepairWithCatalog: 'spiritagent:dock:repair-with-catalog',
    dockRepairWithFiles: 'spiritagent:dock:repair-with-files',
    dockTasks: 'spiritagent:dock:tasks',
    dockActivateTask: 'spiritagent:dock:activate-task',
    desktopAccounts: 'spiritagent:desktop:accounts',
    desktopSwitchAccount: 'spiritagent:desktop:switch-account',
    desktopAddAccount: 'spiritagent:desktop:add-account',
    desktopRemoveAccount: 'spiritagent:desktop:remove-account',
    desktopQuit: 'spiritagent:desktop:quit',
    backgroundReady: 'spiritagent:background:ready',
    backgroundAcknowledge: 'spiritagent:background:acknowledge',

    authActivate: 'spiritagent:auth:activate',
    authRefresh: 'spiritagent:auth:refresh',
    authLogout: 'spiritagent:auth:logout',
    authGetSession: 'spiritagent:auth:get-session',
    gatewayPresence: 'spiritagent:gateway:presence',
    gatewayGetState: 'spiritagent:gateway:get-state',
    api: 'spiritagent:api',
    apiAsset: 'spiritagent:api:asset',
    apiAssetBuffer: 'spiritagent:api:asset-buffer',
    sessionHistoryGet: 'spiritagent:session-history:get',
    sessionHistorySave: 'spiritagent:session-history:save',
    sessionHistoryRemove: 'spiritagent:session-history:remove',
    voicePlaybackGet: 'spiritagent:voice-playback:get',
    voicePlaybackUpdate: 'spiritagent:voice-playback:update',
    voicePlaybackRemove: 'spiritagent:voice-playback:remove',
    readFileDataUrl: 'spiritagent:readFileDataUrl',
    readImageForAttach: 'spiritagent:readImageForAttach',
    registerUserSelectedPaths: 'spiritagent:registerUserSelectedPaths',
    chatSetPendingFeed: 'spiritagent:chat:set-pending-feed',
    chatTakePendingFeed: 'spiritagent:chat:take-pending-feed',
    selectPaths: 'spiritagent:selectPaths',
    writeClipboard: 'spiritagent:writeClipboard',
    copyImage: 'spiritagent:copyImage',
    saveImage: 'spiritagent:saveImage',
    logEmit: 'spiritagent:log:emit',
    version: 'spiritagent:version',
    runnerInvoke: 'spiritagent:runner:invoke',
    runnerGetState: 'spiritagent:runner:get-state',
    runnerConfigRead: 'spiritagent:runner-config:read',
    runnerConfigPatch: 'spiritagent:runner-config:patch',
    prefsApplyServerVoice: 'spiritagent:prefs:apply-server-voice',
    shortcutsGet: 'spiritagent:shortcuts:get',
    shortcutsSet: 'spiritagent:shortcuts:set',
    surfaceOpen: 'spiritagent:surface:open',
    surfaceClose: 'spiritagent:surface:close',
    surfaceMinimize: 'spiritagent:surface:minimize',
    surfaceMaximize: 'spiritagent:surface:maximize',
    surfaceIsMaximized: 'spiritagent:surface:is-maximized',
    surfaceGetState: 'spiritagent:surface:get-state',
    surfaceSetCompanion: 'spiritagent:surface:set-companion',
    surfaceCompanionVisibility: 'spiritagent:surface:companion-visibility',
    surfaceSetIgnoreMouseEvents: 'spiritagent:surface:set-ignore-mouse-events',
    uiStateGet: 'spiritagent:ui-state:get',
    uiStateUpdate: 'spiritagent:ui-state:update',
    uiStateReady: 'spiritagent:ui-state:ready',
    uiStateInputBusy: 'spiritagent:ui-state:input-busy',
    uiStateReply: 'spiritagent:ui-state:reply',
    skillsList: 'spiritagent:skills:list',
    skillSetEnabled: 'spiritagent:skill:set-enabled',
    toolsetsList: 'spiritagent:toolsets:list',
    toolsetSetEnabled: 'spiritagent:toolset:set-enabled',
    mediaPrepareOutput: 'spiritagent:media:prepare-output',
    mediaAudioUpload: 'spiritagent:media:audio-upload',
    mediaVoiceSampleUpload: 'spiritagent:media:voice-sample-upload',
    mediaTts: 'spiritagent:media:tts',
    mediaVideoUpload: 'spiritagent:media:video-upload',
    onboardingAudioRead: 'spiritagent:onboardingAudio:read',
    spriteHide: 'spiritagent:sprite:hide',
    spriteSetIgnoreMouseEvents: 'spiritagent:sprite:set-ignore-mouse-events',
    spriteGetPosition: 'spiritagent:sprite:get-position',
    spriteSetPosition: 'spiritagent:sprite:set-position',
    spriteMoveToCursorDisplay: 'spiritagent:sprite:move-to-cursor-display',
    updateCheck: 'spiritagent:update:check',
    updateDownload: 'spiritagent:update:download',
    updateInstall: 'spiritagent:update:install',
    updateGetState: 'spiritagent:update:get-state'
  } as const satisfies Record<string, IpcChannel>,
  event: {
    presentationChanged: 'spiritagent:presentation:changed',
    presentationPointerPosition: 'spiritagent:presentation:pointer-position',
    dockChanged: 'spiritagent:dock:changed',
    desktopNavigate: 'spiritagent:desktop:navigate',
    backgroundMedia: 'spiritagent:background:media',
    backgroundPlayback: 'spiritagent:presentation:background-playback',

    voicePlaybackChanged: 'spiritagent:voice-playback:changed',
    authChanged: 'spiritagent:auth:changed',
    authSessionExpired: 'spiritagent:auth:session-expired',
    prefsHydrated: 'spiritagent:prefs-hydrated',
    runnerStatus: 'spiritagent:runner:status',
    shortcutsChanged: 'spiritagent:shortcuts:changed',
    surfaceChanged: 'spiritagent:surface:changed',
    surfaceNavigate: 'spiritagent:surface:navigate',
    uiStateRequest: 'spiritagent:ui-state:request',
    spriteDefaultScaleChanged: 'spiritagent:sprite:default-scale-changed',
    chatPendingFeed: 'spiritagent:chat:pending-feed',
    trayActivate: 'spiritagent:tray:activate',
    trayResetPosition: 'spiritagent:tray:reset-position',
    uiThemeChanged: 'spiritagent:ui-theme-changed',
    updateEvent: 'spiritagent:update-event',
    gatewayStateChanged: 'spiritagent:gateway:state-changed',
    gatewayEvent: 'spiritagent:gateway:event'
  } as const satisfies Record<string, IpcEventChannel>,
  send: {
    prefsSet: 'spiritagent:prefs:set',
    uiTheme: 'spiritagent:ui-theme',
    spriteSetDefaultScale: 'spiritagent:sprite:set-default-scale'
  } as const satisfies Record<string, IpcSendChannel>
} as const
