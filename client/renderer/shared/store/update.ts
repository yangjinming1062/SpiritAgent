import { atom } from 'nanostores'

// 设置页展示的更新状态：update-bridge 把主进程的 DesktopUpdateEvent 映射为按 status 区分的变体，
// idle 表示尚未收到任何更新事件。
export type UpdateStatus =
  | { status: 'idle' }
  | { status: 'checking' }
  | {
      status: 'available'
      version: string
      releaseDate?: string
    }
  | { status: 'none'; version?: string }
  | {
      status: 'downloading'
      percent: number
      transferred: number
      total: number
    }
  | { status: 'downloaded'; version: string }
  | { status: 'error'; message: string }

const $updateStatus = atom<UpdateStatus>({ status: 'idle' })

function setUpdateStatus(next: UpdateStatus): void {
  $updateStatus.set(next)
}

function selectTargetVersion(status: UpdateStatus): string {
  return 'version' in status && status.version ? status.version : ''
}

export { $updateStatus, selectTargetVersion, setUpdateStatus }
