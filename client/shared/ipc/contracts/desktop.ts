export interface DesktopVersionInfo {
  appVersion: string
  electronVersion: string
  nodeVersion: string
  platform: string
}

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

export type SurfaceCompanionSide = 'left' | 'right'

export interface SurfaceCompanionPreference {
  enabled: boolean
  side: SurfaceCompanionSide
}

export interface SurfaceCompanionState {
  preference: SurfaceCompanionPreference
  visible: boolean
  hiddenReason: 'layout' | 'minimized' | 'screen-locked' | 'window-hidden' | null
}

export interface DesktopSurfaceChangedEvent {
  companion: SurfaceCompanionState
  open: boolean
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
}

export interface DesktopSpriteRestPosition extends DesktopSpritePosition {
  origin?: { x: number; y: number }
}

export const SPRITE_SCALE_LIMITS = { max: 3, min: 0.3 } as const

// 渲染层偏好写穿透：key 为点键（companion.voice_id / ui.theme 等），value 原样入云同步管道。
export interface SpiritAgentPrefsSet {
  key: string
  value: unknown
}

export interface DesktopServerVoiceSelection {
  accountId: string
  authSessionId: string
  voiceId: string
}

export interface DesktopShortcutsConfig {
  openMain: string
  toggleVisibility: string
}

export const DEFAULT_SHORTCUTS: Readonly<DesktopShortcutsConfig> = {
  openMain: 'Alt+Shift+L',
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
