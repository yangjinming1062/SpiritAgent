export interface DesktopUpdateInfo {
  releaseDate?: string
  version: string
}

export interface DesktopUpdateProgress {
  percent: number
  total: number
  transferred: number
}

export type DesktopUpdatePhase = 'check' | 'download' | 'install'

// preparing：安装包已下载，正在预取并校验同版本 Runner 资产；downloaded 表示两者均已就绪，可重启安装。
export type DesktopUpdateEvent =
  | { info?: DesktopUpdateInfo; type: 'available' }
  | { info?: DesktopUpdateInfo; type: 'downloaded' }
  | { info?: DesktopUpdateInfo; type: 'none' }
  | { info?: DesktopUpdateInfo; type: 'preparing' }
  | { message: string; phase: DesktopUpdatePhase; type: 'error' }
  | { progress: DesktopUpdateProgress; type: 'progress' }
  | { type: 'checking' }
