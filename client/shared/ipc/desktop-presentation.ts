import { isResourceId } from '../../../shared/resource-id'
export type PresentationMode = 'window' | 'desktop'

export interface PresentationDisplay {
  id: number
  label: string
  isPrimary: boolean
  width: number
  height: number
  pixelWidth: number | null
  pixelHeight: number | null
}

export interface PresentationState {
  requestedMode: PresentationMode
  effectiveMode: PresentationMode
  status: 'inactive' | 'starting' | 'active' | 'recovering' | 'failed'
  prepareDesktopMedia: boolean
  failureReason: string | null
  supported: boolean
  unsupportedReason: string | null
  accessibility: { granted: boolean; requestable: boolean }
  foreground: boolean
  stageAvailable: boolean
  fullscreen: boolean
  compatibilityWarning: string | null
  displayId: number | null
  /** 交互屏本地 DIP 坐标；原生工作区尚未就绪或已退出桌面时为空。 */
  workArea: { x: number; y: number; width: number; height: number } | null
  /** 已定位的原生托盘 DIP 尺寸；平台无嵌入托盘时为空。 */
  systemTray: { width: number; height: number; visible: boolean } | null
  displays: PresentationDisplay[]
  stageOwner: 'sprite' | 'desktop'
  stageVisible: boolean
  stageEpoch: number
  revision: number
  switchBlockedReason: string | null
}

export interface DesktopTrayLayout {
  epoch: number
  /** 测量时的视口尺寸 DIP；窗口尺寸变化后旧测量作废。 */
  viewport: { width: number; height: number } | null
  /** 交互屏本地 DIP：完整托盘占位与允许显示的底栏视口。 */
  layout: {
    bounds: { x: number; y: number; width: number; height: number }
    clip: { x: number; y: number; width: number; height: number }
  } | null
}

export interface DesktopMediaReference {
  url: string
}

export interface DesktopMediaBytes {
  bytes: Uint8Array
  mime: string
}

export interface DesktopBackgroundRequest {
  authSessionId: string
  video: DesktopMediaReference | null
  poster: DesktopMediaReference | null
  outfit: DesktopMediaReference | null
  playId: string | null
  /** 成品身份：稳定 ID + 成品版本 + 呈现代次，签名 URL 不参与识别。 */
  visualId: string | null
  visualVersion: number | null
  presentationEpoch: number
  expiresAt: string | null
  kind: 'loop' | 'once'
  continuity: 'base' | 'independent'
  transition: 'natural' | 'fade'
  geometryMode: 'target' | 'keep'
  inputImageHash: string | null
  theme: string
  reduceMotion: boolean
  paused: boolean
  clear: boolean
}

export interface DesktopBackground extends Omit<
  DesktopBackgroundRequest,
  'authSessionId' | 'video' | 'poster' | 'outfit'
> {
  accountEpoch: number
  revision: number
  video: DesktopMediaBytes | null
  poster: DesktopMediaBytes | null
  outfit: DesktopMediaBytes | null
}

export interface DesktopBackgroundPlayback {
  accountEpoch: number
  revision: number
  playId: string
  status: 'first-frame' | 'started' | 'completed' | 'interrupted' | 'failed'
  error?: string
}

export interface DockWindow {
  id: string
  title: string
  minimized: boolean
}

export interface DockEntry {
  id: string
  name: string
  icon: string | null
  status: 'loading' | 'ready' | 'missing' | 'invalid'
  error?: string
  running: boolean
  windows: DockWindow[]
  canPin: boolean
}

export interface DockState {
  revision: number
  pinnedRevision: number
  entries: DockEntry[]
  runningEntries: DockEntry[]
  runningStatus: 'inactive' | 'loading' | 'ready' | 'unavailable'
  runningError: string | null
}

/** Windows Jump List 任务的渲染层投影；启动目标与参数只保留在主进程。 */
export interface DockTask {
  description?: string
  icon: string | null
  id: string
  title: string
}

export type DockCatalogSourceKey =
  | 'start-user'
  | 'start-all'
  | 'desktop-user'
  | 'desktop-onedrive'
  | 'desktop-public'
  | 'app-paths'
  | 'apps-folder'
  | 'mac-applications'
  | 'mac-system'
  | 'mac-user'

export interface DockCatalogSource {
  key: DockCatalogSourceKey
  items: number
  ok: boolean
}

/** 目录条目只带主进程句柄；真实启动目标不出主进程。 */
export interface DockCatalogItem {
  id: string
  name: string
  detail: string
  inDock: boolean
}

export interface DockCatalog {
  revision: number
  sources: DockCatalogSource[]
  items: DockCatalogItem[]
}

export interface DesktopAccount {
  id: string
  username: string
  baseUrl: string
  active: boolean
}

export const APP_PAGES = ['chat', 'life', 'posts', 'diary', 'companion', 'settings'] as const
export type AppPage = (typeof APP_PAGES)[number]
export const SETTINGS_SECTIONS = [
  'companionship',
  'voice',
  'sources',
  'theme',
  'display',
  'devices',
  'capabilities',
  'application',
  'advanced'
] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]
export const SETTINGS_TABS = {
  capabilities: ['skills', 'toolsets'],
  application: ['general', 'shortcuts', 'about']
} as const
export type SettingsTab = (typeof SETTINGS_TABS)[keyof typeof SETTINGS_TABS][number]
export const COMPANION_SECTIONS = [
  'profile',
  'voice',
  'memory',
  'wardrobe',
  'environment',
  'actions',
  'visuals'
] as const
export type CompanionSection = (typeof COMPANION_SECTIONS)[number]

export interface AppNavigationTarget {
  page?: AppPage
  section?: SettingsSection
  companionSection?: CompanionSection
  settingsTab?: SettingsTab
  sessionId?: string
  messageKey?: string
  pursuitId?: string
}

export function normalizeAppNavigation(raw: unknown): AppNavigationTarget {
  if (!raw || typeof raw !== 'object') {
    return {}
  }

  const value = raw as Record<string, unknown>
  const page = APP_PAGES.find(item => item === value.page)
  const section = SETTINGS_SECTIONS.find(item => item === value.section)

  const companionSection = COMPANION_SECTIONS.find(item => item === value.companionSection)

  const settingsTab =
    section === 'capabilities' || section === 'application'
      ? SETTINGS_TABS[section].find(item => item === value.settingsTab)
      : undefined

  return {
    page: page ?? (section ? 'settings' : companionSection ? 'companion' : undefined),
    section,
    companionSection,
    settingsTab,
    sessionId: typeof value.sessionId === 'string' ? value.sessionId : undefined,
    messageKey: typeof value.messageKey === 'string' ? value.messageKey : undefined,
    pursuitId: isResourceId(value.pursuitId) ? value.pursuitId : undefined
  }
}

export interface StageActivity {
  locked: boolean
}
