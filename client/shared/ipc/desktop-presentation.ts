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
