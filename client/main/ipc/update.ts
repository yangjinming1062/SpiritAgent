import { type DesktopUpdateEvent, type DesktopUpdateInfo, IPC, type IpcEventContract } from '@ipc/contracts'
import type { App, IpcMain } from 'electron'
import log from 'electron-log/main'
// 顶层静态 import：client/package.json 是 ESM (`"type": "module"`)，asar 模式下 dynamic require
// （`require('electron-log/main')` / `require('electron-updater')`）会被 Node 拒绝并抛
// "Dynamic require of 'electron-log/main' is not supported"。把这两条搬上来既消除错误，
// 也让 esbuild 在打包期把 CJS 入口转成 ESM-friendly 的 default import。
// 注意：electron-updater 是 CJS 模块没有 named export `autoUpdater`，必须 default import + 解构，
// 顶层 named import 在 dev/prod 都会被 Node ESM loader 拒绝。
import electronUpdaterPkg from 'electron-updater'
import type { ProgressInfo } from 'electron-updater'

import { errorMessage } from '../shared/utils'

interface UpdateIpcDeps {
  electron: { app: App }
  ipcMain: IpcMain
  isFeedConfigured?: () => boolean
  broadcast: <C extends keyof IpcEventContract>(channel: C, ...payload: IpcEventContract[C]) => void
}

/** electron-updater 的 info 携带渲染层不消费的字段；只挑契约承诺的两个字段下发，
 *  避免 releaseNotes（string | ReleaseNoteInfo[]）等原始结构跨入 IPC 边界。 */
function toDesktopUpdateInfo(info: unknown): DesktopUpdateInfo {
  const candidate = (info ?? {}) as Partial<DesktopUpdateInfo>

  return {
    releaseDate: typeof candidate.releaseDate === 'string' ? candidate.releaseDate : undefined,
    version: typeof candidate.version === 'string' ? candidate.version : ''
  }
}

export function registerUpdateIpc({ electron, ipcMain, isFeedConfigured, broadcast }: UpdateIpcDeps): void {
  const { app } = electron
  let latestEvent: DesktopUpdateEvent | null = null

  // 更新状态的唯一消费方是生活空间设置页，广播到所有窗口而不是假定主窗口：
  // 消费方窗口由渲染层装配决定（update-bridge 挂在哪个入口哪个窗口收得到）。
  function broadcastUpdate(event: DesktopUpdateEvent): void {
    latestEvent = event
    broadcast(IPC.event.updateEvent, event)
  }

  // 自动检查可能早于生活空间开窗；新窗口订阅后读最新快照，补齐开窗前的状态。
  ipcMain.handle(IPC.invoke.updateGetState, () => latestEvent)

  // 始终注册 updateCheck：开发模式无更新源，回 'none' 让渲染层落 "up to date" 文案，
  // 避免 renderer 触发未注册 IPC handler 抛出 unhandled rejection。
  ipcMain.handle(IPC.invoke.updateCheck, async () => {
    if (!app.isPackaged || (isFeedConfigured && !isFeedConfigured())) {
      broadcastUpdate({ type: 'none' })

      return
    }

    try {
      await autoUpdater.checkForUpdates()
    } catch (e: unknown) {
      const msg = errorMessage(e)
      broadcastUpdate({ message: msg, type: 'error' })
    }
  })

  if (!app.isPackaged) {
    return
  }

  const { autoUpdater } = electronUpdaterPkg
  autoUpdater.logger = log

  autoUpdater.on('checking-for-update', () => broadcastUpdate({ type: 'checking' }))
  autoUpdater.on('update-available', info => broadcastUpdate({ info: toDesktopUpdateInfo(info), type: 'available' }))
  autoUpdater.on('update-not-available', info => broadcastUpdate({ info: toDesktopUpdateInfo(info), type: 'none' }))
  autoUpdater.on(
    'download-progress',
    // 同 toDesktopUpdateInfo：契约只承诺三个字段，剔除 bytesPerSecond / delta 等原始结构。
    (raw: ProgressInfo) =>
      broadcastUpdate({
        progress: { percent: raw.percent, total: raw.total, transferred: raw.transferred },
        type: 'progress'
      })
  )
  autoUpdater.on('update-downloaded', info => broadcastUpdate({ info: toDesktopUpdateInfo(info), type: 'downloaded' }))
  autoUpdater.on('error', (err: unknown) => {
    const message = errorMessage(err)

    if (message.includes('404') && message.includes('latest.yml')) {
      broadcastUpdate({ info: undefined, type: 'none' })
    } else {
      broadcastUpdate({ message, type: 'error' })
    }
  })
}
