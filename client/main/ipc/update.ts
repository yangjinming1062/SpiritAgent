import { type DesktopUpdateEvent, type DesktopUpdateInfo, IPC, type IpcEventContract } from '@ipc/contracts'
import type { App, IpcMain } from 'electron'
import log from 'electron-log/main'
// 产物为 ESM：electron-updater 是 CJS 包，只能静态 default import 后解构 autoUpdater。
import electronUpdaterPkg from 'electron-updater'
import type { ProgressInfo } from 'electron-updater'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

// 更新源来自激活时保存的后端地址；设置页把该文案拼在「检查更新失败」之后。
const FEED_UNAVAILABLE_MESSAGE = { en: 'activation required', zh: '请先激活' } as const

interface UpdateIpcDeps {
  electron: { app: App }
  ipcMain: IpcMain
  /** 按当前保存的后端地址配置更新源；尚无地址时返回 false。 */
  ensureFeedConfigured: () => boolean
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

export function registerUpdateIpc({ electron, ipcMain, ensureFeedConfigured, broadcast }: UpdateIpcDeps): void {
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

  // 始终注册 updateCheck，避免渲染层调用未注册的 handler 抛出 unhandled rejection。
  ipcMain.handle(IPC.invoke.updateCheck, async () => {
    // 开发构建没有更新源，回 'none'。
    if (!app.isPackaged) {
      broadcastUpdate({ type: 'none' })

      return
    }

    if (!ensureFeedConfigured()) {
      const language = store.read().language === 'en' ? 'en' : 'zh'

      broadcastUpdate({ message: FEED_UNAVAILABLE_MESSAGE[language], type: 'error' })

      return
    }

    // checkForUpdates 失败时先发 'error' 事件再抛出；只由下方监听器上报，避免重复广播覆盖 404 映射。
    await autoUpdater.checkForUpdates().catch(() => {})
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
