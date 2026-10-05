import type { DockState } from '@ipc/contracts'

/** 原生窗口信息只在主进程内流转；渲染层只接收 Dock 投影。 */
export interface RunningApplicationWindow {
  id: string
  appId: string
  target: string | null
  name: string
  title: string
  minimized: boolean
  lastActive: number
}

export interface RunningApplicationsState {
  status: DockState['runningStatus']
  error: string | null
  windows: RunningApplicationWindow[]
}
