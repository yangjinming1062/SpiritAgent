// SpiritAgent Electron IPC 契约 —— 主进程与渲染进程的唯一真理源。
// 通过 `@ipc/contracts` 别名同时被 `client/main/preload.ts` 和
// `client/renderer/shared/types/global.d.ts` 导入。
// 在此处新增或重命名通道/载荷字段，会在两侧类型检查时立即报错。

import { clamp } from '../runtime'
import type { SpeechStyle } from '../speech-style'
export type { SpeechStyle } from '../speech-style'

export interface DesktopVersionInfo {
  appVersion: string
  electronVersion: string
  nodeVersion: string
  platform: string
}

export interface DesktopUpdateInfo {
  releaseDate?: string
  releaseNotes?: string
  version: string
}

export interface DesktopUpdateProgress {
  bytesPerSecond: number
  delta: number
  percent: number
  total: number
  transferred: number
}

export type DesktopUpdateEvent =
  | { info?: DesktopUpdateInfo; type: 'available' }
  | { info?: DesktopUpdateInfo; type: 'downloaded' }
  | { info?: DesktopUpdateInfo; type: 'none' }
  | { message: string; type: 'error' }
  | { progress: DesktopUpdateProgress; type: 'progress' }
  | { type: 'checking' }

interface CapabilityHealthItem {
  available: boolean
  reason?: string | null
}

export type RunnerCapabilitiesHealth = Record<string, CapabilityHealthItem>

export interface RunnerCapabilities {
  microphone?: boolean
  platform?: string
  python?: string
  screen_capture?: boolean
  system_activity?: boolean
}

export type DesktopRunnerStatusEvent =
  | { error: Error; phase: string; type: 'error' }
  | {
      capabilities?: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed?: boolean | null
      runnerVersion?: null | string
      tools?: unknown[] | null
      type: 'runner_ready'
    }
  | {
      capabilities?: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed?: boolean | null
      runnerVersion?: null | string
      tools: unknown[] | null
      type: 'running'
    }
  | { errors?: string[]; reason?: string; type: 'stopped' }

export type DesktopRunnerPhase = 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'

export interface DesktopRunnerState {
  capabilities?: null | RunnerCapabilities
  capabilitiesHealth?: null | RunnerCapabilitiesHealth
  lastError?: null | string
  phase: DesktopRunnerPhase
  probeFailed?: boolean | null
  runnerVersion?: null | string
  startedAt?: null | number
  stoppedAt?: null | number
}

/** 主进程内部连接缓存：含会话 JWT，仅在 main 使用，禁止直发渲染进程。 */
export interface SpiritAgentConnection {
  baseUrl: string
  isFullscreen: boolean
  nativeOverlayWidth: number
  token: null | string
  windowButtonPosition: null | { x: number; y: number }
  wsUrl: string
}

/** 渲染进程可见的连接投影——刻意不含 token（ARCHITECTURE §3 隐藏凭证）。 */
export type SpiritAgentConnectionPublic = Omit<SpiritAgentConnection, 'token'>

export type SpiritAgentUiPalette = 'night' | 'day'
export type SpiritAgentUiEffect = 'solid' | 'clear'
export type SpiritAgentUiTheme = 'night' | 'day' | 'night-clear' | 'day-clear'

// 主进程侧校验白名单——契约是跨进程唯一真理源，渲染层 registry 只扩展元数据。
export const SPIRITAGENT_UI_THEMES = [
  'night',
  'day',
  'night-clear',
  'day-clear'
] as const satisfies readonly SpiritAgentUiTheme[]

export function getUiPalette(theme: SpiritAgentUiTheme): SpiritAgentUiPalette {
  return theme === 'night' || theme === 'night-clear' ? 'night' : 'day'
}

export function getUiEffect(theme: SpiritAgentUiTheme): SpiritAgentUiEffect {
  return theme === 'night-clear' || theme === 'day-clear' ? 'clear' : 'solid'
}

export function normalizeUiTheme(raw: unknown): SpiritAgentUiTheme {
  if (raw === 'day' || raw === 'classic-light' || raw === 'lilac-glass') {
    return 'day'
  }

  if (raw === 'night' || raw === 'classic' || raw === 'cyber-glass' || raw === 'holo') {
    return 'night'
  }

  if (raw === 'night-clear') {
    return 'night-clear'
  }

  return 'day-clear'
}

// 入口面：互斥的两个 BrowserWindow。"closed" 仅渲染层用作占位，不进主进程 IPC 边界。
export type SurfaceId = 'living' | 'workbench'
export const SPIRITAGENT_SURFACES = ['living', 'workbench'] as const satisfies readonly SurfaceId[]

export function normalizeSurfaceId(raw: unknown): SurfaceId {
  return raw === 'workbench' ? 'workbench' : 'living'
}

export interface DesktopSurfaceOpenPayload {
  sessionId?: string
  surface: SurfaceId
  view?: string
}

export interface DesktopSurfaceBounds {
  displayId: number
  height: number
  width: number
  x: number
  y: number
}

export interface DesktopSurfaceChangedEvent {
  /** 工作台窗口屏幕坐标边界。生活空间不下发，工作台显示、移动或调整尺寸时下发。 */
  bounds?: DesktopSurfaceBounds | null
  lastSurface: SurfaceId
  open: null | SurfaceId
}

export function getThemeBackgroundColor(theme?: SpiritAgentUiTheme): string {
  if (theme === 'night' || theme === 'night-clear') {
    return '#0e0f14'
  }

  if (theme === 'day') {
    return '#f8f7f5'
  }

  return '#f4f6fa'
}

export interface DesktopUiThemeBroadcast {
  theme: SpiritAgentUiTheme
}

// 渲染层偏好写穿透：key 为点键（companion.voice_id / ui.theme 等），value 原样入云同步管道。
export interface SpiritAgentPrefsSet {
  key: string
  value: unknown
}

// 云端配置水合广播：主进程把 GET /api/config 水合进本地镜像后，携带镜像中的偏好节通知双窗口刷新缓存。
export interface DesktopShortcutsConfig {
  openLiving: string
  openWorkbench: string
  toggleVisibility: string
}

export const DEFAULT_SHORTCUTS: Readonly<DesktopShortcutsConfig> = {
  openLiving: 'Alt+Shift+L',
  openWorkbench: 'Alt+Shift+W',
  toggleVisibility: 'Alt+Shift+H'
} as const

export interface ShortcutRegistrationStatus {
  error?: string
  registered: boolean
}

export interface DesktopShortcutsState {
  config: DesktopShortcutsConfig
  status: Record<keyof DesktopShortcutsConfig, ShortcutRegistrationStatus>
}

export interface DesktopShortcutsSetPayload {
  shortcuts: Partial<DesktopShortcutsConfig>
}

export interface DesktopPrefsHydrated {
  companion: Record<string, unknown>
  shortcuts?: DesktopShortcutsConfig
  ui: { theme?: SpiritAgentUiTheme }
  // 顶层原始值同步键（PROTOCOL §1.4），从 user_settings.language 透传过来；
  // null/undefined 表示云端未设置（回落 DEFAULT_LOCALE）。
  language?: null | string
}

export interface DesktopBootProgress {
  error: null | string
  message: string
  phase: string
  progress: number
  running: boolean
  timestamp: number
}

// 启动进度的 0–100 收口；非数与 NaN 一律视为 0，避免上游 NaN 透传把进度条钉死。
// 与 DesktopBootProgress.progress 字段配套使用，跨主进程广播与渲染层水合复用。
export function clampBootProgress(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }

  return clamp(Math.round(value), 0, 100)
}

export interface DesktopAuthSnapshot {
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | { username: string }
}

export interface DesktopActivatePayload {
  code: string
}

export interface DesktopAuthBroadcast {
  authenticated: boolean
  snapshot: DesktopAuthSnapshot | null
}

export interface SpiritAgentApiRequest {
  body?: unknown
  method?: string
  path: string
  timeoutMs?: number
}

export interface SpiritAgentSelectPathsOptions {
  defaultPath?: string
  directories?: boolean
  filters?: Array<{ extensions: string[]; name: string }>
  multiple?: boolean
  title?: string
}

export interface SkillItem {
  category: string
  compatible: boolean
  description?: string
  enabled: boolean
  name: string
  platforms?: null | string[]
}

export interface ToolsetItem {
  enabled: boolean
  id: string
  toolNames: string[]
}

export interface RunnerConfigPatch {
  op?: 'delete' | 'set'
  path: readonly (number | string)[]
  value?: unknown
}

export interface MediaSttPayload {
  context?: null | string
  dataUrl: string
  filename?: string
  language?: string
}

export interface MediaTtsPayload {
  speech_style?: SpeechStyle
  context?: null | string
  language?: string
  persist?: boolean
  text: string
  voice?: string
}

export interface AttachmentVideoUploadPayload {
  path: string
  sessionId: string
}

export interface AttachmentVideoUploadResult {
  fileId: string
  mime: string
  size: number
  url: string
}

export type DesktopGatewayState = 'closed' | 'connecting' | 'error' | 'idle' | 'open'

export interface DesktopGatewayEvent<P = unknown> {
  payload?: P
  seq?: number
  session_id?: string
  type: string
}

export interface DesktopGatewayRpcRequest {
  id: number
  method: string
  params?: Record<string, unknown>
}

export interface DesktopGatewayRpcResponse {
  error?: string
  id: number
  ok: boolean
  result?: unknown
}

// 1. 请求-响应（渲染进程 -> 主进程，通过 ipcRenderer.invoke / ipcMain.handle）
export interface IpcInvokeContract {
  // 连接与启动
  'spiritagent:connection': () => SpiritAgentConnectionPublic | Promise<SpiritAgentConnectionPublic>
  'spiritagent:gateway:ws-url': () => Promise<string> | string
  'spiritagent:gateway:request': (payload: {
    method: string
    params?: Record<string, unknown>
  }) => Promise<unknown> | unknown
  'spiritagent:gateway:get-state': () => DesktopGatewayState | Promise<DesktopGatewayState>
  'spiritagent:boot-progress:get': () => DesktopBootProgress | Promise<DesktopBootProgress>

  // 鉴权
  'spiritagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:refresh': () => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:logout': () =>
    | { backendUnreachable?: boolean; error?: string; ok: boolean }
    | Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }>
  'spiritagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>

  // 入口面（互斥 living / workbench）
  'spiritagent:surface:open': (payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  'spiritagent:surface:toggle': (payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  'spiritagent:surface:close': () => Promise<void> | void
  'spiritagent:surface:focus': () => Promise<void> | void
  'spiritagent:surface:minimize': () => Promise<void> | void
  'spiritagent:surface:maximize': () => Promise<void> | void
  'spiritagent:surface:is-maximized': () => Promise<boolean> | boolean
  'spiritagent:surface:get-state': () => DesktopSurfaceChangedEvent | Promise<DesktopSurfaceChangedEvent>
  'spiritagent:surface:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void

  // 后端 API 代理
  'spiritagent:api': (request: SpiritAgentApiRequest) => Promise<unknown> | unknown
  'spiritagent:api:asset': (request: {
    preferCache?: boolean
    cacheOnly?: boolean
    contentHash?: string
    url: string
  }) => Promise<string> | string
  'spiritagent:api:asset-buffer': (request: { preferCache?: boolean; contentHash?: string; url: string }) => Promise<Uint8Array> | Uint8Array
  'spiritagent:api:asset-model-url': (request: { contentHash?: string; url: string }) => string | Promise<string>

  // 文件 / 剪贴板 / 日志
  'spiritagent:readFileDataUrl': (filePath: string) => Promise<string> | string
  'spiritagent:readImageForAttach': (filePath: string) => Promise<string> | string
  'spiritagent:registerUserSelectedPaths': (paths: string[]) => Promise<void> | void
  /** 精灵窗投喂的混合文件路径信箱：写入后由生活空间窗口取走，解决跨窗口内存不共享。 */
  'spiritagent:chat:set-pending-feed': (paths: string[]) => Promise<void> | void
  'spiritagent:chat:take-pending-feed': () => Promise<string[]> | string[]
  'spiritagent:selectPaths': (options?: SpiritAgentSelectPathsOptions) => Promise<string[]> | string[]
  'spiritagent:writeClipboard': (text: string) => boolean | Promise<boolean>
  'spiritagent:saveClipboardImage': () => Promise<string> | string
  'spiritagent:log:emit': (payload: {
    args: unknown[]
    level: 'error' | 'info' | 'warn'
    scope: string
  }) => Promise<void> | void
  'spiritagent:version': () => DesktopVersionInfo | Promise<DesktopVersionInfo>

  // Runner
  'spiritagent:runner:invoke': (name: string, args: Record<string, unknown>) => Promise<unknown> | unknown
  'spiritagent:runner:cancel': () => unknown | Promise<unknown>
  'spiritagent:runner:get-state': () => DesktopRunnerState | Promise<DesktopRunnerState>
  'spiritagent:runner:get-tools': () => Array<Record<string, unknown>> | Promise<Array<Record<string, unknown>>>
  'spiritagent:runner-config:read': () =>
    | { content?: string; error?: string; ok: boolean }
    | Promise<{ content?: string; error?: string; ok: boolean }>
  'spiritagent:runner-config:write': (
    configString: string
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>
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
  'spiritagent:media:stt': (payload: MediaSttPayload) => { text: string } | Promise<{ text: string }>
  'spiritagent:media:tts': (
    payload: MediaTtsPayload
  ) => { dataUrl: string; mimeType: string } | Promise<{ dataUrl: string; mimeType: string }>
  'spiritagent:media:video-upload': (
    payload: AttachmentVideoUploadPayload
  ) => AttachmentVideoUploadResult | Promise<AttachmentVideoUploadResult>
  'spiritagent:onboardingAudio:read': (
    tag: string
  ) =>
    | { bytes: number; dataUrl: string; mimeType: string; tag: string }
    | Promise<{ bytes: number; dataUrl: string; mimeType: string; tag: string }>

  // 快捷键
  'spiritagent:shortcuts:get': () => DesktopShortcutsState | Promise<DesktopShortcutsState>
  'spiritagent:shortcuts:set': (
    payload: DesktopShortcutsSetPayload
  ) => DesktopShortcutsState | Promise<DesktopShortcutsState>
  'spiritagent:shortcuts:reset': () => DesktopShortcutsState | Promise<DesktopShortcutsState>

  // 更新
  'spiritagent:update:check': () => Promise<void> | void

  // 精灵窗口
  'spiritagent:sprite:hide': () => Promise<void> | void
  'spiritagent:sprite:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void
  'spiritagent:sprite:get-position': () =>
    | null
    | { origin?: { x: number; y: number }; x: number; y: number }
    | Promise<null | { origin?: { x: number; y: number }; x: number; y: number }>
  'spiritagent:sprite:set-position': (payload: { x: number; y: number }) => Promise<void> | void
  'spiritagent:sprite:move-to-cursor-display': () =>
    | null
    | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }
    | Promise<null | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }>
}

// 2. 主进程向渲染进程推送事件（通过 webContents.send / ipcRenderer.on）
export interface IpcEventContract {
  'spiritagent:auth:changed': [payload: DesktopAuthBroadcast]
  'spiritagent:auth:session-expired': []
  'spiritagent:boot-progress': [payload: DesktopBootProgress]
  'spiritagent:power-resume': []
  'spiritagent:prefs-hydrated': [payload: DesktopPrefsHydrated]
  'spiritagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'spiritagent:shortcuts:changed': [payload: DesktopShortcutsState]
  'spiritagent:surface:changed': [payload: DesktopSurfaceChangedEvent]
  'spiritagent:chat:pending-feed': [payload: string[]]
  'spiritagent:tray:activate': []
  'spiritagent:tray:logout': []
  'spiritagent:tray:reset-position': []
  'spiritagent:ui-theme-changed': [payload: DesktopUiThemeBroadcast]
  'spiritagent:update-event': [payload: DesktopUpdateEvent]
  'spiritagent:gateway:state-changed': [payload: { state: DesktopGatewayState }]
  'spiritagent:gateway:event': [payload: { event: DesktopGatewayEvent }]
  'spiritagent:gateway:rpc-dispatch': [payload: DesktopGatewayRpcRequest]
}

// 3. 渲染进程向主进程单向发送消息（通过 ipcRenderer.send / ipcMain.on）
export interface IpcSendContract {
  'spiritagent:prefs:set': [payload: SpiritAgentPrefsSet]
  'spiritagent:ui-theme': [payload: SpiritAgentUiTheme]
  'spiritagent:gateway:broadcast-state': [payload: { state: DesktopGatewayState }]
  'spiritagent:gateway:broadcast-event': [payload: { event: DesktopGatewayEvent }]
  'spiritagent:gateway:rpc-reply': [payload: DesktopGatewayRpcResponse]
}

type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
export type IpcSendChannel = keyof IpcSendContract

// 运行时 channel 常量。用扁平键(camelCase)避免 `Record<string, Record<string, ...>>`
// 守卫无法适配混合扁平/嵌套 channel 名的结构问题。每个叶子字符串都必须
// 是对应契约接口的合法 key,任何拼写错误立即在 `satisfies` 检查处报错。
// 在 main + preload 中以 `IPC.invoke.authActivate` 等方式使用,完全消除字面量字符串。
export const IPC = {
  invoke: {
    authActivate: 'spiritagent:auth:activate',
    authRefresh: 'spiritagent:auth:refresh',
    authLogout: 'spiritagent:auth:logout',
    authGetSession: 'spiritagent:auth:get-session',
    connection: 'spiritagent:connection',
    gatewayWsUrl: 'spiritagent:gateway:ws-url',
    gatewayRequest: 'spiritagent:gateway:request',
    gatewayGetState: 'spiritagent:gateway:get-state',
    bootProgressGet: 'spiritagent:boot-progress:get',
    api: 'spiritagent:api',
    apiAsset: 'spiritagent:api:asset',
    apiAssetBuffer: 'spiritagent:api:asset-buffer',
    apiAssetModelUrl: 'spiritagent:api:asset-model-url',
    readFileDataUrl: 'spiritagent:readFileDataUrl',
    readImageForAttach: 'spiritagent:readImageForAttach',
    registerUserSelectedPaths: 'spiritagent:registerUserSelectedPaths',
    chatSetPendingFeed: 'spiritagent:chat:set-pending-feed',
    chatTakePendingFeed: 'spiritagent:chat:take-pending-feed',
    selectPaths: 'spiritagent:selectPaths',
    writeClipboard: 'spiritagent:writeClipboard',
    saveClipboardImage: 'spiritagent:saveClipboardImage',
    logEmit: 'spiritagent:log:emit',
    version: 'spiritagent:version',
    runnerInvoke: 'spiritagent:runner:invoke',
    runnerCancel: 'spiritagent:runner:cancel',
    runnerGetState: 'spiritagent:runner:get-state',
    runnerGetTools: 'spiritagent:runner:get-tools',
    runnerConfigRead: 'spiritagent:runner-config:read',
    runnerConfigWrite: 'spiritagent:runner-config:write',
    runnerConfigPatch: 'spiritagent:runner-config:patch',
    shortcutsGet: 'spiritagent:shortcuts:get',
    shortcutsSet: 'spiritagent:shortcuts:set',
    shortcutsReset: 'spiritagent:shortcuts:reset',
    surfaceOpen: 'spiritagent:surface:open',
    surfaceToggle: 'spiritagent:surface:toggle',
    surfaceClose: 'spiritagent:surface:close',
    surfaceFocus: 'spiritagent:surface:focus',
    surfaceMinimize: 'spiritagent:surface:minimize',
    surfaceMaximize: 'spiritagent:surface:maximize',
    surfaceIsMaximized: 'spiritagent:surface:is-maximized',
    surfaceGetState: 'spiritagent:surface:get-state',
    surfaceSetIgnoreMouseEvents: 'spiritagent:surface:set-ignore-mouse-events',
    skillsList: 'spiritagent:skills:list',
    skillSetEnabled: 'spiritagent:skill:set-enabled',
    toolsetsList: 'spiritagent:toolsets:list',
    toolsetSetEnabled: 'spiritagent:toolset:set-enabled',
    mediaStt: 'spiritagent:media:stt',
    mediaTts: 'spiritagent:media:tts',
    mediaVideoUpload: 'spiritagent:media:video-upload',
    onboardingAudioRead: 'spiritagent:onboardingAudio:read',
    spriteHide: 'spiritagent:sprite:hide',
    spriteSetIgnoreMouseEvents: 'spiritagent:sprite:set-ignore-mouse-events',
    spriteGetPosition: 'spiritagent:sprite:get-position',
    spriteSetPosition: 'spiritagent:sprite:set-position',
    spriteMoveToCursorDisplay: 'spiritagent:sprite:move-to-cursor-display',
    updateCheck: 'spiritagent:update:check'
  } as const satisfies Record<string, IpcChannel>,
  event: {
    authChanged: 'spiritagent:auth:changed',
    authSessionExpired: 'spiritagent:auth:session-expired',
    bootProgress: 'spiritagent:boot-progress',
    powerResume: 'spiritagent:power-resume',
    prefsHydrated: 'spiritagent:prefs-hydrated',
    runnerStatus: 'spiritagent:runner:status',
    shortcutsChanged: 'spiritagent:shortcuts:changed',
    surfaceChanged: 'spiritagent:surface:changed',
    chatPendingFeed: 'spiritagent:chat:pending-feed',
    trayActivate: 'spiritagent:tray:activate',
    trayLogout: 'spiritagent:tray:logout',
    trayResetPosition: 'spiritagent:tray:reset-position',
    uiThemeChanged: 'spiritagent:ui-theme-changed',
    updateEvent: 'spiritagent:update-event',
    gatewayStateChanged: 'spiritagent:gateway:state-changed',
    gatewayEvent: 'spiritagent:gateway:event',
    gatewayRpcDispatch: 'spiritagent:gateway:rpc-dispatch'
  } as const satisfies Record<string, IpcEventChannel>,
  send: {
    prefsSet: 'spiritagent:prefs:set',
    uiTheme: 'spiritagent:ui-theme',
    gatewayBroadcastState: 'spiritagent:gateway:broadcast-state',
    gatewayBroadcastEvent: 'spiritagent:gateway:broadcast-event',
    gatewayRpcReply: 'spiritagent:gateway:rpc-reply'
  } as const satisfies Record<string, IpcSendChannel>
} as const
