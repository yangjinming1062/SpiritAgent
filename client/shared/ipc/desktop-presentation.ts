export type PresentationMode = 'window' | 'desktop'

export interface PresentationDisplay {
  id: number
  label: string
  width: number
  height: number
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
  displays: PresentationDisplay[]
  stageOwner: 'sprite' | 'desktop'
  stageVisible: boolean
  stageEpoch: number
  revision: number
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
  setEpoch: number
  expiresAt: string | null
  kind: 'loop' | 'once'
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

// 入口面：互斥的两个 BrowserWindow；都未打开时以 null 表示。
export type SurfaceId = 'living' | 'workbench'

export function normalizeSurfaceId(raw: unknown): SurfaceId {
  return raw === 'workbench' ? 'workbench' : 'living'
}

export interface DesktopNavigation {
  surface: SurfaceId
  view?: string
  sessionId?: string
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
