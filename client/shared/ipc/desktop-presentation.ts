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
  failureReason: string | null
  supported: boolean
  foreground: boolean
  stageAvailable: boolean
  fullscreen: boolean
  companionAlwaysOnTop: boolean
  stageInsets: DesktopStageInsets
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

export interface DesktopStageInsets {
  top: number
  bottom: number
  left: number
  right: number
}

export type DesktopCompanionInteraction =
  | { kind: 'toggle-whisper' | 'hide' }
  | { kind: 'menu'; x: number; y: number }
  | { kind: 'drop'; paths: string[] }

export interface DesktopBackground {
  image: string | null
  theme: string
  reduceMotion: boolean
}

export interface DockEntry {
  id: string
  name: string
  icon: string | null
  status: 'ready' | 'missing' | 'invalid'
  error?: string
}

export interface DockState {
  revision: number
  entries: DockEntry[]
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

export interface DesktopNavigation {
  surface: 'living' | 'workbench'
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

export interface StageRitualRequest {
  callId: string
  epoch: number
  rect: { x: number; y: number; w: number; h: number }
}
