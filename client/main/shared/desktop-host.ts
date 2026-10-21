import type { SpiritAgentUiPalette } from '@ipc/contracts'

import type { RunningApplicationsState } from './desktop-applications'
import type { DesktopJumpListTask } from './desktop-jump-list'

export interface DesktopHostWindow {
  handle: Buffer
  role: 'background' | 'overlay'
  /** Windows 使用原生物理坐标，macOS 使用全局左上原点 points；工作区与表面采用同一坐标系。 */
  bounds: { x: number; y: number; width: number; height: number }
}

export interface DesktopHostSystemTray {
  bounds: DesktopHostWindow['bounds']
  visible: boolean
}

export interface DesktopHostTrayLayout {
  bounds: DesktopHostWindow['bounds']
  clip: DesktopHostWindow['bounds']
}

export interface DesktopHostOptions {
  helperPath: string
  journalPath: string
  log: (message: string) => void
  onFailure: (reason: string) => void
  onForegroundChanged?: (state: { active: boolean; stageAvailable: boolean; fullscreen: boolean }) => void
  onWarning?: (reason: string) => void
  onApplicationsChanged?: (state: RunningApplicationsState) => void
  onWorkAreaChanged?: (bounds: DesktopHostWindow['bounds']) => void
  onSystemTrayChanged?: (tray: DesktopHostSystemTray | null) => void
}

export interface DesktopHostOperations {
  start: (options: {
    windows: DesktopHostWindow[]
    parentPid: number
    takeover: boolean
    palette: SpiritAgentUiPalette
    workArea: DesktopHostWindow['bounds']
  }) => Promise<void>
  setPalette: (palette: SpiritAgentUiPalette) => Promise<void>
  setTrayLayout: (layout: DesktopHostTrayLayout | null, eligible?: () => boolean) => Promise<void>
  focus: (handle: Buffer, eligible?: () => boolean) => Promise<boolean>
  refreshApplications: (eligible: () => boolean) => Promise<void>
  activateExternal: (
    windowId: string,
    allowRemote: boolean,
    eligible: () => boolean,
    continuation?: () => boolean
  ) => Promise<void>
  cancelExternalInteraction: () => Promise<void>
  launchExternal: (
    target: string,
    appId: string,
    eligible: () => boolean,
    continuation?: () => boolean
  ) => Promise<void>
  closeExternal: (windowIds: string[], eligible: () => boolean) => Promise<void>
  getJumpListTasks: (target: string, eligible: () => boolean) => Promise<DesktopJumpListTask[]>
  activateJumpListTask: (target: string, taskId: string, eligible: () => boolean) => Promise<void>
  stop: () => Promise<void>
  recover: () => Promise<boolean>
}

export interface DesktopHost extends DesktopHostOperations {
  availability: () => { supported: boolean; reason: string | null }
  accessibility: () => { granted: boolean; requestable: boolean }
  requestAccessibility: () => void
}
