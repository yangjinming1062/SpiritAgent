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

export type DesktopStageLayer = 'scene' | 'actor' | 'cinematic' | 'effects'

export interface DesktopStageLayout {
  fit: 'contain' | 'cover'
  actorPosition: [number, number]
  safeArea: [number, number, number, number]
  layerOrder: DesktopStageLayer[]
}

/** 服务器未下发 layout 时的兜底；与后端 StageCompositionLayout 默认值一致。 */
export const DEFAULT_STAGE_LAYOUT: DesktopStageLayout = {
  fit: 'contain',
  actorPosition: [0.5, 1],
  safeArea: [0, 0, 1, 1],
  layerOrder: ['scene', 'actor', 'effects']
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
  /** 分层主体；video 此时仅用于独立环境，完整影像不叠加主体。 */
  actor: DesktopMediaReference | null
  actorBaseKey: string | null
  layout: DesktopStageLayout
  playId: string | null
  playTarget: 'background' | 'actor'
  /** 成品身份：稳定 ID + 成品版本 + 呈现代次，签名 URL 不参与识别。 */
  visualId: string | null
  visualVersion: number | null
  /** 主体层成品身份；仅分层模式有值。 */
  actorVisualId: string | null
  actorVisualVersion: number | null
  presentationEpoch: number
  expiresAt: string | null
  kind: 'loop' | 'once'
  /** 主体层的播放方式；短动作为 once，常驻为 loop。 */
  actorKind: 'loop' | 'once'
  continuity: 'base' | 'independent'
  transition: 'natural' | 'fade'
  geometryMode: 'target' | 'keep'
  inputImageHash: string | null
  theme: string
  reduceMotion: boolean
  paused: boolean
  backgroundKind: 'scene' | 'cinematic'
  /** 背景已是按本地时段挑选的媒体版本；true 时壁纸不再叠加时段色调滤镜。 */
  phaseMedia: boolean
  clear: boolean
}

export interface DesktopBackground extends Omit<
  DesktopBackgroundRequest,
  'authSessionId' | 'video' | 'poster' | 'outfit' | 'actor'
> {
  accountEpoch: number
  revision: number
  video: DesktopMediaBytes | null
  poster: DesktopMediaBytes | null
  outfit: DesktopMediaBytes | null
  actor: DesktopMediaBytes | null
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

export const APP_PAGES = ['chat', 'life', 'creation', 'companion', 'settings'] as const
export type AppPage = (typeof APP_PAGES)[number]
export const LIFE_SECTIONS = ['now', 'posts', 'diary'] as const
export type LifeSection = (typeof LIFE_SECTIONS)[number]
export const CREATION_SECTIONS = ['visuals', 'wardrobe', 'environment', 'actions'] as const
export type CreationSection = (typeof CREATION_SECTIONS)[number]
export const COMPANION_SECTIONS = ['profile', 'voice', 'memory'] as const
export type CompanionSection = (typeof COMPANION_SECTIONS)[number]
export const SETTINGS_SECTIONS = [
  'companionship',
  'appearance',
  'sources',
  'devices',
  'capabilities',
  'advanced'
] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]
export const SETTINGS_TABS = {
  companionship: ['together', 'voice'],
  capabilities: ['skills', 'toolsets'],
  advanced: ['general', 'shortcuts', 'about']
} as const
export type SettingsTab = (typeof SETTINGS_TABS)[keyof typeof SETTINGS_TABS][number]

export const DEFAULT_SECTION = {
  life: 'now',
  creation: 'visuals',
  companion: 'profile',
  settings: 'companionship'
} as const satisfies {
  life: LifeSection
  creation: CreationSection
  companion: CompanionSection
  settings: SettingsSection
}

/** 持久位置：只描述在哪一页、哪个分区、哪个页签；不含任何定位细节。 */
export type AppLocation =
  | { page: 'chat' }
  | { page: 'life'; section: LifeSection }
  | { page: 'creation'; section: CreationSection }
  | { page: 'companion'; section: CompanionSection }
  | { page: 'settings'; section: SettingsSection; tab?: SettingsTab }

/** 瞬时定位：按页面约束的联合类型，不允许跨页携带。 */
export type PageFocus =
  | { page: 'chat'; sessionId?: string; messageKey?: string }
  | { page: 'life'; pursuitId?: string }
  | { page: 'creation'; outfitId?: string; sceneId?: string; visualId?: string }
  | { page: 'companion' }
  | { page: 'settings' }

export interface AppNavigationTarget {
  location: AppLocation
  focus: PageFocus
}

export const DEFAULT_APP_LOCATION: AppLocation = { page: 'chat' }
export const DEFAULT_NAVIGATION: AppNavigationTarget = {
  location: DEFAULT_APP_LOCATION,
  focus: { page: 'chat' }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function matches<T extends string>(values: readonly T[], value: unknown): value is T {
  return values.some(item => item === value)
}

export function normalizeAppLocation(raw: unknown): AppLocation {
  const value = isRecord(raw) ? raw : {}

  switch (value.page) {
    case 'life':
      return { page: 'life', section: matches(LIFE_SECTIONS, value.section) ? value.section : DEFAULT_SECTION.life }

    case 'creation':
      return {
        page: 'creation',
        section: matches(CREATION_SECTIONS, value.section) ? value.section : DEFAULT_SECTION.creation
      }

    case 'companion':
      return {
        page: 'companion',
        section: matches(COMPANION_SECTIONS, value.section) ? value.section : DEFAULT_SECTION.companion
      }
    case 'settings': {
      const section = matches(SETTINGS_SECTIONS, value.section) ? value.section : DEFAULT_SECTION.settings
      const tabs = (SETTINGS_TABS as Partial<Record<SettingsSection, readonly SettingsTab[]>>)[section] ?? []
      const tab = matches(tabs, value.tab) ? value.tab : undefined

      return tab ? { page: 'settings', section, tab } : { page: 'settings', section }
    }

    default:
      return DEFAULT_APP_LOCATION
  }
}

export function normalizePageFocus(raw: unknown, location: AppLocation): PageFocus {
  const value = isRecord(raw) ? raw : {}

  if (value.page !== location.page) {
    return { page: location.page } as PageFocus
  }

  switch (location.page) {
    case 'chat':
      return {
        page: 'chat',
        sessionId: typeof value.sessionId === 'string' ? value.sessionId : undefined,
        messageKey: typeof value.messageKey === 'string' ? value.messageKey : undefined
      }

    case 'life':
      return { page: 'life', pursuitId: isResourceId(value.pursuitId) ? value.pursuitId : undefined }

    case 'creation':
      return {
        page: 'creation',
        outfitId: isResourceId(value.outfitId) ? value.outfitId : undefined,
        sceneId: isResourceId(value.sceneId) ? value.sceneId : undefined,
        visualId: isResourceId(value.visualId) ? value.visualId : undefined
      }

    default:
      return { page: location.page } as PageFocus
  }
}

/** 唯一合成点：位置与定位都先规范化；页面不一致时整体丢弃定位，不同步、不合并。 */
export function composeNavigation(location: AppLocation, focus: PageFocus | undefined): AppNavigationTarget {
  const next = normalizeAppLocation(location)

  return { location: next, focus: normalizePageFocus(focus, next) }
}

export function normalizeAppNavigation(raw: unknown): AppNavigationTarget {
  const value = isRecord(raw) ? raw : {}
  const location = normalizeAppLocation(value.location)

  return composeNavigation(location, (value.focus ?? undefined) as PageFocus | undefined)
}

/** 导航 query 的唯一读写表：页面决定该页允许携带哪些定位键。 */
const NAV_QUERY_KEYS = {
  chat: ['sessionId', 'messageKey'],
  life: ['pursuitId'],
  creation: ['outfitId', 'sceneId', 'visualId'],
  companion: [],
  settings: []
} as const satisfies Record<AppPage, readonly string[]>

const SETTINGS_TAB_KEY = 'subtab'

/** 位置与定位的唯一序列化点；返回不含前导 `#` 的 hash 内容。 */
export function navigationHash(target: AppNavigationTarget): string {
  const { location, focus } = composeNavigation(target.location, target.focus)
  const section = 'section' in location ? `/${location.section}` : ''
  const query = new URLSearchParams()
  const tab = 'tab' in location ? location.tab : undefined

  if (tab) {
    query.set(SETTINGS_TAB_KEY, tab)
  }

  const values = focus as Record<string, unknown>

  for (const key of NAV_QUERY_KEYS[focus.page]) {
    const value = values[key]

    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  }

  return `/${location.page}${section}${query.size ? `?${query}` : ''}`
}

/** 与 navigationHash 对称的解析点；无法识别的 hash 返回 null，由调用方保留现状。 */
export function parseNavigationHash(hash: string): AppNavigationTarget | null {
  const [path, query] = hash.replace(/^#\/?/, '').split('?')
  const [page, section] = path.split('/')

  if (!matches(APP_PAGES, page)) {
    return null
  }

  const params = new URLSearchParams(query)
  const location = normalizeAppLocation({ page, section, tab: params.get(SETTINGS_TAB_KEY) ?? undefined })
  const focus: Record<string, unknown> = { page }

  for (const key of NAV_QUERY_KEYS[page]) {
    const value = params.get(key)

    if (value !== null) {
      focus[key] = value
    }
  }

  return composeNavigation(location, focus as PageFocus)
}

export interface StageActivity {
  locked: boolean
}
