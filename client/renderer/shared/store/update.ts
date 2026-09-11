import { atom } from 'nanostores'

// 可辨识联合类型——主进程将 electron-updater 事件以 `{ type, ... }` 载荷
// 通过 `spiritagent:update-event` IPC 通道转发。
// 每个变体对应一个 autoUpdater.on(...) 回调。
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
