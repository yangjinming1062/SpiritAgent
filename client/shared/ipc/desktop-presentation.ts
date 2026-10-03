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
  displayId: number | null
  displays: PresentationDisplay[]
  stageOwner: 'sprite' | 'desktop'
  stageVisible: boolean
  stageEpoch: number
  revision: number
}

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

/** 应用目录来源：开始菜单（当前用户／All Users）与桌面（用户／OneDrive 重定向／公共）。 */
export type DockCatalogSourceKey = 'start-user' | 'start-all' | 'desktop-user' | 'desktop-onedrive' | 'desktop-public'

export interface DockCatalogSource {
  key: DockCatalogSourceKey
  items: number
  ok: boolean
}

/** 目录条目只带主进程句柄；真实 `.lnk` 与目标路径不出主进程。 */
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
