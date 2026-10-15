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
  foreground: boolean
  stageAvailable: boolean
  fullscreen: boolean
  compatibilityWarning: string | null
  companionActivity: DesktopCompanionActivityState
  voicePreparing: boolean
  displayId: number | null
  /** 交互屏本地 DIP 坐标；原生工作区尚未就绪或已退出桌面时为空。 */
  workArea: { x: number; y: number; width: number; height: number } | null
  displays: PresentationDisplay[]
  stageOwner: 'sprite' | 'desktop'
  stageVisible: boolean
  stageEpoch: number
  revision: number
  switchBlockedReason: string | null
}

export type DesktopCompanionActivityState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'working' | 'disconnected'

export const DESKTOP_COMPANION_ACTIVITY_PRIORITY: Record<DesktopCompanionActivityState, number> = {
  idle: 10,
  listening: 40,
  thinking: 50,
  speaking: 60,
  working: 70,
  disconnected: 100
}

export interface DesktopMediaReference {
  url: string
  contentHash?: string
}

export interface DesktopMediaBytes {
  bytes: Uint8Array
  mime: string
}

export interface DesktopBackgroundRequest {
  authSessionId: string
  video: DesktopMediaReference | null
  poster: DesktopMediaReference | null
  playId: string | null
  setId: number | null
  actionId: number | null
  actionVersion: number | null
  setEpoch: number
  expiresAt: string | null
  kind: 'loop' | 'once'
  continuity: 'base' | 'independent'
  transition: 'base' | 'fade'
  baseVersion: number | null
  theme: string
  reduceMotion: boolean
  paused: boolean
  clear: boolean
}

export interface DesktopBackground extends Omit<DesktopBackgroundRequest, 'authSessionId' | 'video' | 'poster'> {
  accountEpoch: number
  revision: number
  video: DesktopMediaBytes | null
  poster: DesktopMediaBytes | null
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

export const APP_PAGES = ['chat', 'companion', 'posts', 'settings'] as const
export type AppPage = (typeof APP_PAGES)[number]
export const SETTINGS_SECTIONS = ['companionship', 'voice', 'display', 'devices', 'application', 'advanced'] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]
export const COMPANION_SECTIONS = ['home', 'profile', 'appearance', 'scene', 'desktop-life', 'memory'] as const
export type CompanionSection = (typeof COMPANION_SECTIONS)[number]
export type PostsTab = 'posts' | 'diary'

export interface AppNavigationTarget {
  page?: AppPage
  section?: SettingsSection
  companionSection?: CompanionSection
  postsTab?: PostsTab
  companionTask?: 'create'
  returnToDesktopLife?: boolean
  settingsTab?: 'skills' | 'toolsets'
  sessionId?: string
  messageKey?: string
}

export type DesktopNavigation = AppNavigationTarget

export function normalizeAppNavigation(raw: unknown): AppNavigationTarget {
  if (!raw || typeof raw !== 'object') {
    return {}
  }

  const value = raw as Record<string, unknown>
  const page = APP_PAGES.find(item => item === value.page)
  const section = SETTINGS_SECTIONS.find(item => item === value.section)
  const companionSection = COMPANION_SECTIONS.find(item => item === value.companionSection)
  const postsTab = value.postsTab === 'posts' || value.postsTab === 'diary' ? value.postsTab : undefined
  const settingsTab = value.settingsTab === 'skills' || value.settingsTab === 'toolsets' ? value.settingsTab : undefined

  return {
    page: page ?? (section ? 'settings' : companionSection ? 'companion' : postsTab ? 'posts' : undefined),
    section,
    companionSection,
    postsTab,
    companionTask: value.companionTask === 'create' ? 'create' : undefined,
    returnToDesktopLife: value.returnToDesktopLife === true ? true : undefined,
    settingsTab: section === 'devices' ? settingsTab : undefined,
    sessionId: typeof value.sessionId === 'string' ? value.sessionId : undefined,
    messageKey: typeof value.messageKey === 'string' ? value.messageKey : undefined
  }
}

export interface StageActivity {
  focus: {
    category: 'ide' | 'music' | 'reader' | 'gaming' | 'browsing' | 'other' | 'unknown'
    fullscreen: boolean
    windowGeom?: { x: number; y: number; w: number; h: number }
    windowId?: string
    windowPid?: number
    runnerInstanceId?: string
  } | null
  locked: boolean
  idleSeconds: number
  effectiveTier: 'autonomous' | 'normal' | 'still'
}
