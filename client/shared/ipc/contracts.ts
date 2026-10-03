import type {
  DesktopAccount,
  DesktopBackground,
  DesktopNavigation,
  DockCatalog,
  DockState,
  PresentationMode,
  PresentationState,
  StageActivity,
  StageRitualRequest
} from './desktop-presentation'
export type {
  DesktopAccount,
  DesktopBackground,
  DesktopNavigation,
  DockCatalog,
  DockCatalogItem,
  DockCatalogSource,
  DockCatalogSourceKey,
  DockEntry,
  DockState,
  PresentationDisplay,
  PresentationMode,
  PresentationState,
  StageActivity,
  StageRitualRequest
} from './desktop-presentation'

// SpiritAgent Electron IPC 契约 —— 主进程与渲染进程的唯一真理源。通过 `@ipc/contracts` 别名同时被 `client/main/preload.ts` 和 `client/renderer/shared/types/global.d.ts` 导入。在此处新增或重命名通道/载荷字段，会在两侧类型检查时立即报错。

export interface MemoryToolScope {
  user_id: number
  system_preset_id: string
}

export interface DesktopVersionInfo {
  appVersion: string
  electronVersion: string
  nodeVersion: string
  platform: string
}

export interface DesktopUpdateInfo {
  releaseDate?: string
  version: string
}

/** 会话历史磁盘快照：主进程缓存，渲染层本地秒开 + 增量合并的载荷。 */
export interface SessionHistorySnapshot {
  currentSeq: number
  info?: Record<string, unknown>
  lastMessageId: null | number
  messages: unknown[]
  nextCursor: null | string
  truncated: boolean
  writtenAt: number
}

export interface VoicePlaybackRecord {
  listened: boolean
  positionSeconds: number
}

export interface VoicePlaybackSnapshot {
  records: Record<string, VoicePlaybackRecord>
  revision: number
}

export interface VoicePlaybackScope {
  authSessionId: string
  sessionId: string
}

export interface VoicePlaybackUpdate extends VoicePlaybackScope, VoicePlaybackRecord {
  messageId: number
  bubbleIndex: number
}

export interface VoicePlaybackRemoval extends VoicePlaybackScope {
  messageIds?: number[]
}

export interface VoicePlaybackChanged extends VoicePlaybackScope {
  snapshot: VoicePlaybackSnapshot
}

export function voicePlaybackKey(messageId: number, bubbleIndex: number): string {
  return `${messageId}:${bubbleIndex}`
}

export interface DesktopUpdateProgress {
  percent: number
  total: number
  transferred: number
}

export type DesktopUpdatePhase = 'check' | 'download' | 'install'

// preparing：安装包已下载，正在预取并校验同版本 Runner 资产；downloaded 表示两者均已就绪，可重启安装。
export type DesktopUpdateEvent =
  | { info?: DesktopUpdateInfo; type: 'available' }
  | { info?: DesktopUpdateInfo; type: 'downloaded' }
  | { info?: DesktopUpdateInfo; type: 'none' }
  | { info?: DesktopUpdateInfo; type: 'preparing' }
  | { message: string; phase: DesktopUpdatePhase; type: 'error' }
  | { progress: DesktopUpdateProgress; type: 'progress' }
  | { type: 'checking' }

export interface DesktopRunnerStatusEvent {
  type: 'error' | 'runner_ready' | 'running' | 'stopped' | 'stopping'
}

export type DesktopRunnerPhase = 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'

export interface DesktopRunnerState {
  phase: DesktopRunnerPhase
}

/** 模型派发的一次 Runner 工具调用，按 `callId` 查询、认领调用日志并可单独取消。 */
export interface RunnerCallRequest {
  args: Record<string, unknown>
  callId: string
  name: string
  skillScope?: MemoryToolScope
}

/** Runner 调用结局（PROTOCOL「调用日志与未知结果」）：`failed` 是 Runner 明确报告的工具失败或执行前拒绝；`not_executed` 是请求发出前 Runner 未连接；超时、取消、断连、他处持有与日志判定未知都归 `unknown`。 */
export type RunnerCallOutcome =
  | { error: string; status: 'failed' }
  | { result: unknown; status: 'completed' }
  | { status: 'not_executed' }
  | { status: 'unknown' }

export type SpiritAgentUiPalette = 'night' | 'day'
export type SpiritAgentUiEffect = 'solid' | 'clear'
export type SpiritAgentUiTheme = 'night' | 'day' | 'night-clear' | 'day-clear'

// 主进程侧校验白名单——契约是跨进程唯一真理源；主题名称与描述在渲染层文案字典。
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
  return raw === 'night' || raw === 'day' || raw === 'night-clear' ? raw : 'day-clear'
}

/** 入口 HTML 播种参数名：主进程 loadURL 前把镜像里的主题写进查询串，渲染层首帧前消费。 */
export const UI_THEME_URL_PARAM = 'ui_theme'

// 入口面：互斥的两个 BrowserWindow；都未打开时以 null 表示。
export type SurfaceId = 'living' | 'workbench'

export function normalizeSurfaceId(raw: unknown): SurfaceId {
  return raw === 'workbench' ? 'workbench' : 'living'
}

export interface DesktopSurfaceOpenPayload {
  sessionId?: string
  surface: SurfaceId
  view?: string
}

export type SurfaceCompanionSide = 'left' | 'right'

export interface SurfaceCompanionPreference {
  enabled: boolean
  side: SurfaceCompanionSide
}

export interface SurfaceCompanionState {
  preference: SurfaceCompanionPreference
  visible: boolean
  slotWidth: number
  outerWidth: number
  hiddenReason: 'edge' | 'maximized' | 'minimized' | 'screen-locked' | 'window-hidden' | null
}

export interface SurfacePlaybackClaim {
  playId: string
  expiresAt: string | null
}

export interface DesktopSurfaceChangedEvent {
  companions: Record<SurfaceId, SurfaceCompanionState>
  open: null | SurfaceId
  openVisible: boolean
  revision: number
  screenLocked: boolean
  /** 桌面精灵窗实际可见：窗口存在、未隐藏且未最小化；完整入口收起舞台不改变此值。 */
  spriteVisible: boolean
}

export interface DesktopUiThemeBroadcast {
  theme: SpiritAgentUiTheme
}

export interface DesktopSpriteScalePayload {
  scale: number
}

export interface DesktopSpritePosition {
  x: number
  y: number
  screenEdge?: { side: 'left' | 'right'; yRatio: number }
}

export interface DesktopSpriteRestPosition extends DesktopSpritePosition {
  origin?: { x: number; y: number }
}

export interface DesktopWindowSceneSnapshot {
  runnerInstanceId: string
  viewport: { displayId: number; height: number; scaleFactor: number; width: number; x: number; y: number }
  windows: Array<{
    focused: boolean
    h: number
    id: string
    displayId: number
    pid: number
    visible: boolean
    w: number
    x: number
    y: number
    zOrder: number
  }>
}

// 屏幕矩形：入参为 Runner 原生屏幕坐标，出参为精灵视口内 DIP 坐标。
export interface DesktopScreenRect {
  h: number
  w: number
  x: number
  y: number
}

export const SPRITE_SCALE_LIMITS = { max: 3, min: 0.3 } as const

// 渲染层偏好写穿透：key 为点键（companion.voice_id / ui.theme 等），value 原样入云同步管道。
export interface SpiritAgentPrefsSet {
  key: string
  value: unknown
}

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

// 云端配置水合广播：只携带渲染层需要回写的伙伴偏好与语言。主题与快捷键由主进程各自的专用同步通道处理。
export interface DesktopPrefsHydrated {
  accountId: string | null
  companion: Record<string, unknown>
  // 顶层原始值同步键（PROTOCOL「配置所有权与云同步」），从 user_settings.language 透传过来；null/undefined 表示云端未设置（回落 DEFAULT_LOCALE）。
  language?: null | string
}

export interface DesktopAuthSnapshot {
  accountId: string
  baseUrl: null | string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: null | number
  user: null | { username: string }
}

export interface DesktopActivatePayload {
  code: string
}

export interface DesktopLogoutPayload {
  expectedSessionId?: string
  reason: 'expired' | 'user'
}

export interface DesktopAuthBroadcast {
  authenticated: boolean
  /** 仅明确移除账户时携带；各窗口删除该账户的持久缓存。 */
  removedAccountId?: string
  snapshot: DesktopAuthSnapshot | null
}

export interface SpiritAgentApiRequest {
  body?: unknown
  method?: string
  path: string
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
  dataUrl: string
  filename?: string
  language?: string
}

export interface MediaTtsPayload {
  context?: null | string
  persist?: boolean
  text: string
  voice?: string
}

export interface AttachmentVideoUploadPayload {
  path: string
  sessionId: string
}

export interface AttachmentVideoUploadResult {
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
  'spiritagent:presentation:ritual-cancel': (callId: string) => void
  'spiritagent:presentation:get-stage-activity': () => StageActivity
  'spiritagent:presentation:set-stage-visible': (visible: boolean) => void
  'spiritagent:presentation:get-state': () => Promise<PresentationState>
  'spiritagent:presentation:set-mode': (mode: PresentationMode) => Promise<PresentationState>
  'spiritagent:presentation:set-display': (id: number) => Promise<PresentationState>
  'spiritagent:presentation:report-ready': () => void
  'spiritagent:presentation:focus': (epoch: number) => Promise<boolean>
  'spiritagent:presentation:heartbeat': () => void
  'spiritagent:presentation:host-ready': () => Promise<void>
  'spiritagent:presentation:set-background': (background: DesktopBackground) => void
  'spiritagent:presentation:claim-play': (claim: SurfacePlaybackClaim) => boolean
  'spiritagent:presentation:stage-activity': (activity: StageActivity) => void
  'spiritagent:presentation:ritual-request': (request: Omit<StageRitualRequest, 'epoch'>) => Promise<boolean>
  'spiritagent:presentation:ritual-complete': (reply: { callId: string; epoch: number; completed: boolean }) => void
  'spiritagent:dock:get-state': () => Promise<DockState>
  'spiritagent:dock:catalog': (force?: boolean) => Promise<DockCatalog>
  'spiritagent:dock:catalog-icons': (ids: string[]) => Promise<Record<string, string | null>>
  'spiritagent:dock:add-from-catalog': (ids: string[]) => Promise<DockState>
  'spiritagent:dock:add-from-files': () => Promise<DockState>
  'spiritagent:dock:add-dropped': (paths: string[]) => Promise<DockState>
  'spiritagent:dock:launch': (id: string) => Promise<void>
  'spiritagent:dock:reorder': (ids: string[]) => Promise<DockState>
  'spiritagent:dock:remove': (id: string) => Promise<DockState>
  'spiritagent:dock:repair-with-catalog': (entryId: string, catalogId: string) => Promise<DockState>
  'spiritagent:dock:repair-with-files': (entryId: string) => Promise<DockState>
  'spiritagent:desktop:accounts': () => Promise<DesktopAccount[]>
  'spiritagent:desktop:switch-account': (id: string) => Promise<void>
  'spiritagent:desktop:add-account': () => Promise<void>
  'spiritagent:desktop:quit': () => void
  'spiritagent:background:ready': () => void

  // 连接与启动
  'spiritagent:gateway:ws-url': () => Promise<string> | string
  'spiritagent:gateway:request': (payload: {
    method: string
    params?: Record<string, unknown>
  }) => Promise<unknown> | unknown
  'spiritagent:gateway:get-state': () => DesktopGatewayState | Promise<DesktopGatewayState>

  // 鉴权
  'spiritagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:refresh': () => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:logout': (
    payload: DesktopLogoutPayload
  ) =>
    | { backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }
    | Promise<{ backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }>
  'spiritagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>

  // 入口面（互斥 living / workbench）
  'spiritagent:surface:open': (payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  'spiritagent:surface:close': () => Promise<void> | void
  'spiritagent:surface:minimize': () => Promise<void> | void
  'spiritagent:surface:maximize': () => Promise<void> | void
  'spiritagent:surface:is-maximized': () => Promise<boolean> | boolean
  'spiritagent:surface:get-state': () => DesktopSurfaceChangedEvent | Promise<DesktopSurfaceChangedEvent>
  'spiritagent:surface:set-companion': (
    preference: SurfaceCompanionPreference
  ) => DesktopSurfaceChangedEvent | Promise<DesktopSurfaceChangedEvent>
  'spiritagent:surface:claim-play': (claim: SurfacePlaybackClaim) => boolean | Promise<boolean>
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
  }) => Promise<string | null> | string | null
  'spiritagent:api:asset-buffer': (request: {
    preferCache?: boolean
    contentHash?: string
    url: string
  }) => Promise<Uint8Array> | Uint8Array

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
  /** 精灵窗投喂的混合文件路径信箱：写入后由生活空间窗口取走，解决跨窗口内存不共享。 */
  'spiritagent:chat:set-pending-feed': (paths: string[]) => Promise<void> | void
  'spiritagent:chat:take-pending-feed': () => Promise<string[]> | string[]
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
  'spiritagent:runner:dispatch-call': (request: RunnerCallRequest) => Promise<RunnerCallOutcome> | RunnerCallOutcome
  'spiritagent:runner:cancel': (callId: string) => unknown | Promise<unknown>
  'spiritagent:runner:get-state': () => DesktopRunnerState | Promise<DesktopRunnerState>
  'spiritagent:runner:get-tools': () => Array<Record<string, unknown>> | Promise<Array<Record<string, unknown>>>
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

  // 更新
  'spiritagent:update:check': () => Promise<void> | void
  'spiritagent:update:download': () => Promise<void> | void
  /** 仅生活空间可调用，且须处于 downloaded 状态。 */
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
  'spiritagent:sprite:get-window-scene': () =>
    | DesktopWindowSceneSnapshot
    | null
    | Promise<DesktopWindowSceneSnapshot | null>
  'spiritagent:sprite:map-screen-rect': (
    rect: DesktopScreenRect
  ) => DesktopScreenRect | null | Promise<DesktopScreenRect | null>
  'spiritagent:sprite:move-to-display': (point: { x: number; y: number }) => Promise<void> | void
  'spiritagent:sprite:move-to-cursor-display': () =>
    | null
    | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }
    | Promise<null | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }>
}

// 2. 主进程向渲染进程推送事件（通过 webContents.send / ipcRenderer.on）
export interface IpcEventContract {
  'spiritagent:presentation:ritual-cancelled': [payload: { callId: string; epoch: number }]
  'spiritagent:presentation:changed': [payload: PresentationState]
  'spiritagent:dock:changed': [payload: DockState]
  'spiritagent:desktop:navigate': [payload: DesktopNavigation]
  'spiritagent:background:image': [payload: DesktopBackground]
  'spiritagent:presentation:stage-activity': [payload: StageActivity]
  'spiritagent:presentation:ritual': [payload: StageRitualRequest]

  'spiritagent:voice-playback:changed': [payload: VoicePlaybackChanged]
  'spiritagent:auth:changed': [payload: DesktopAuthBroadcast]
  'spiritagent:auth:session-expired': [sessionId: string]
  'spiritagent:power-resume': []
  'spiritagent:prefs-hydrated': [payload: DesktopPrefsHydrated]
  'spiritagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'spiritagent:shortcuts:changed': [payload: DesktopShortcutsState]
  'spiritagent:surface:changed': [payload: DesktopSurfaceChangedEvent]
  'spiritagent:sprite:default-scale-changed': [payload: DesktopSpriteScalePayload]
  'spiritagent:chat:pending-feed': [payload: string[]]
  'spiritagent:tray:activate': []
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
  'spiritagent:sprite:set-default-scale': [payload: DesktopSpriteScalePayload]
  'spiritagent:gateway:broadcast-state': [payload: { state: DesktopGatewayState }]
  'spiritagent:gateway:broadcast-event': [payload: { event: DesktopGatewayEvent }]
  'spiritagent:gateway:rpc-reply': [payload: DesktopGatewayRpcResponse]
}

type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
type IpcSendChannel = keyof IpcSendContract

// 运行时 channel 常量。用扁平键(camelCase)避免 `Record<string, Record<string, ...>>` 守卫无法适配混合扁平/嵌套 channel 名的结构问题。每个叶子字符串都必须是对应契约接口的合法 key,任何拼写错误立即在 `satisfies` 检查处报错。在 main + preload 中以 `IPC.invoke.authActivate` 等方式使用,完全消除字面量字符串。
export const IPC = {
  invoke: {
    presentationRitualCancel: 'spiritagent:presentation:ritual-cancel',
    presentationGetStageActivity: 'spiritagent:presentation:get-stage-activity',
    presentationSetStageVisible: 'spiritagent:presentation:set-stage-visible',
    presentationGetState: 'spiritagent:presentation:get-state',
    presentationSetMode: 'spiritagent:presentation:set-mode',
    presentationSetDisplay: 'spiritagent:presentation:set-display',
    presentationReportReady: 'spiritagent:presentation:report-ready',
    presentationFocus: 'spiritagent:presentation:focus',
    presentationHeartbeat: 'spiritagent:presentation:heartbeat',
    presentationHostReady: 'spiritagent:presentation:host-ready',
    presentationSetBackground: 'spiritagent:presentation:set-background',
    presentationClaimPlay: 'spiritagent:presentation:claim-play',
    presentationStageActivity: 'spiritagent:presentation:stage-activity',
    presentationRitualRequest: 'spiritagent:presentation:ritual-request',
    presentationRitualComplete: 'spiritagent:presentation:ritual-complete',
    dockGetState: 'spiritagent:dock:get-state',
    dockCatalog: 'spiritagent:dock:catalog',
    dockCatalogIcons: 'spiritagent:dock:catalog-icons',
    dockAddFromCatalog: 'spiritagent:dock:add-from-catalog',
    dockAddFromFiles: 'spiritagent:dock:add-from-files',
    dockAddDropped: 'spiritagent:dock:add-dropped',
    dockLaunch: 'spiritagent:dock:launch',
    dockReorder: 'spiritagent:dock:reorder',
    dockRemove: 'spiritagent:dock:remove',
    dockRepairWithCatalog: 'spiritagent:dock:repair-with-catalog',
    dockRepairWithFiles: 'spiritagent:dock:repair-with-files',
    desktopAccounts: 'spiritagent:desktop:accounts',
    desktopSwitchAccount: 'spiritagent:desktop:switch-account',
    desktopAddAccount: 'spiritagent:desktop:add-account',
    desktopQuit: 'spiritagent:desktop:quit',
    backgroundReady: 'spiritagent:background:ready',

    authActivate: 'spiritagent:auth:activate',
    authRefresh: 'spiritagent:auth:refresh',
    authLogout: 'spiritagent:auth:logout',
    authGetSession: 'spiritagent:auth:get-session',
    gatewayWsUrl: 'spiritagent:gateway:ws-url',
    gatewayRequest: 'spiritagent:gateway:request',
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
    runnerDispatchCall: 'spiritagent:runner:dispatch-call',
    runnerCancel: 'spiritagent:runner:cancel',
    runnerGetState: 'spiritagent:runner:get-state',
    runnerGetTools: 'spiritagent:runner:get-tools',
    runnerConfigRead: 'spiritagent:runner-config:read',
    runnerConfigPatch: 'spiritagent:runner-config:patch',
    shortcutsGet: 'spiritagent:shortcuts:get',
    shortcutsSet: 'spiritagent:shortcuts:set',
    surfaceOpen: 'spiritagent:surface:open',
    surfaceClose: 'spiritagent:surface:close',
    surfaceMinimize: 'spiritagent:surface:minimize',
    surfaceMaximize: 'spiritagent:surface:maximize',
    surfaceIsMaximized: 'spiritagent:surface:is-maximized',
    surfaceGetState: 'spiritagent:surface:get-state',
    surfaceSetCompanion: 'spiritagent:surface:set-companion',
    surfaceClaimPlay: 'spiritagent:surface:claim-play',
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
    spriteGetWindowScene: 'spiritagent:sprite:get-window-scene',
    spriteMapScreenRect: 'spiritagent:sprite:map-screen-rect',
    spriteMoveToDisplay: 'spiritagent:sprite:move-to-display',
    spriteMoveToCursorDisplay: 'spiritagent:sprite:move-to-cursor-display',
    updateCheck: 'spiritagent:update:check',
    updateDownload: 'spiritagent:update:download',
    updateInstall: 'spiritagent:update:install',
    updateGetState: 'spiritagent:update:get-state'
  } as const satisfies Record<string, IpcChannel>,
  event: {
    presentationRitualCancelled: 'spiritagent:presentation:ritual-cancelled',
    presentationChanged: 'spiritagent:presentation:changed',
    dockChanged: 'spiritagent:dock:changed',
    desktopNavigate: 'spiritagent:desktop:navigate',
    backgroundImage: 'spiritagent:background:image',
    presentationStageActivity: 'spiritagent:presentation:stage-activity',
    presentationRitual: 'spiritagent:presentation:ritual',

    voicePlaybackChanged: 'spiritagent:voice-playback:changed',
    authChanged: 'spiritagent:auth:changed',
    authSessionExpired: 'spiritagent:auth:session-expired',
    powerResume: 'spiritagent:power-resume',
    prefsHydrated: 'spiritagent:prefs-hydrated',
    runnerStatus: 'spiritagent:runner:status',
    shortcutsChanged: 'spiritagent:shortcuts:changed',
    surfaceChanged: 'spiritagent:surface:changed',
    spriteDefaultScaleChanged: 'spiritagent:sprite:default-scale-changed',
    chatPendingFeed: 'spiritagent:chat:pending-feed',
    trayActivate: 'spiritagent:tray:activate',
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
    spriteSetDefaultScale: 'spiritagent:sprite:set-default-scale',
    gatewayBroadcastState: 'spiritagent:gateway:broadcast-state',
    gatewayBroadcastEvent: 'spiritagent:gateway:broadcast-event',
    gatewayRpcReply: 'spiritagent:gateway:rpc-reply'
  } as const satisfies Record<string, IpcSendChannel>
} as const
