import type { DesktopUpdatePhase } from '@ipc/contracts'
import { atom } from 'nanostores'

// 设置页展示的更新状态：update-bridge 把主进程的 DesktopUpdateEvent 映射为按 status 区分的变体，
// idle 表示尚未收到任何更新事件；preparing 为安装包已下载、本机组件尚在预取校验。
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
  | { status: 'preparing'; version: string }
  | { status: 'downloaded'; version: string }
  | { status: 'error'; phase: DesktopUpdatePhase; message: string }

const $updateStatus = atom<UpdateStatus>({ status: 'idle' })

function setUpdateStatus(next: UpdateStatus): void {
  $updateStatus.set(next)
}

export { $updateStatus, setUpdateStatus }
