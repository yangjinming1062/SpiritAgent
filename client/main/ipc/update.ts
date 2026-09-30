import { type DesktopUpdateEvent, type DesktopUpdateInfo, type DesktopUpdatePhase, IPC } from '@ipc/contracts'
import { type App, autoUpdater as electronAutoUpdater, type IpcMain, type WebContents } from 'electron'
import log from 'electron-log/main'
// 产物为 ESM：electron-updater 是 CJS 包，只能静态 default import 后解构 autoUpdater。
import electronUpdaterPkg from 'electron-updater'
import type { ProgressInfo } from 'electron-updater'

import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows, createSerialQueue, errorMessage } from '../shared/utils'

// 更新源来自激活时保存的后端地址；设置页把该文案拼在对应阶段的失败提示之后。
const FEED_UNAVAILABLE_MESSAGE = { en: 'activation required', zh: '请先激活' } as const
// electron-updater 的错误可能内嵌堆栈与响应头：界面只给首行并限长，完整错误已由其 logger 写入 electron-log。
const ERROR_MESSAGE_MAX_LENGTH = 200

/** 更新源与 Runner 资产端口，由 lifecycle/auto-updater 实现。 */
interface UpdateFeedPort {
  /** 按当前保存的后端地址配置更新源；尚无地址时返回 false。 */
  ensureFeedConfigured: () => boolean
  /** 待下载的版本信息是否来自当前更新源。 */
  isAvailableUpdateCurrent: () => boolean
  /** 预取并校验与已下载安装包同版本的 Runner 资产。 */
  prefetchRunnerAssets: (version: string) => Promise<void>
}

interface UpdateIpcDeps {
  electron: { app: App }
  feed: UpdateFeedPort
  ipcMain: IpcMain
  /** 重启安装只接受生活空间窗口的请求。 */
  isInstallSender: (sender: WebContents) => boolean
  /** 置应用退出标志，使窗口关闭拦截放行。 */
  markQuitting: () => void
}

/** electron-updater 的 info 携带渲染层不消费的字段；只挑契约承诺的两个字段下发，避免 releaseNotes（string | ReleaseNoteInfo[]）等原始结构跨入 IPC 边界。 */
function toDesktopUpdateInfo(info: unknown): DesktopUpdateInfo {
  const candidate = (info ?? {}) as Partial<DesktopUpdateInfo>

  return {
    releaseDate: typeof candidate.releaseDate === 'string' ? candidate.releaseDate : undefined,
    version: typeof candidate.version === 'string' ? candidate.version : ''
  }
}

function summarizeError(error: unknown): string {
  const firstLine = errorMessage(error).split('\n', 1)[0].trim()

  return firstLine.length > ERROR_MESSAGE_MAX_LENGTH ? `${firstLine.slice(0, ERROR_MESSAGE_MAX_LENGTH)}…` : firstLine
}

export function registerUpdateIpc({ electron, feed, ipcMain, isInstallSender, markQuitting }: UpdateIpcDeps): void {
  const { app } = electron
  let latestEvent: DesktopUpdateEvent | null = null
  let phase: DesktopUpdatePhase = 'check'
  // Runner 预取串行执行，避免重试时并发清理同一暂存目录。
  const enqueuePrepare = createSerialQueue()

  // 更新状态的唯一消费方是生活空间设置页；广播到所有窗口而非假定主窗口，消费方由渲染层装配决定（update-bridge 挂在哪个入口哪个窗口收得到）。
  function broadcastUpdate(event: DesktopUpdateEvent): void {
    latestEvent = event
    broadcastToAllWindows(IPC.event.updateEvent, event)
  }

  function broadcastError(errorPhase: DesktopUpdatePhase, error: unknown): void {
    broadcastUpdate({ message: summarizeError(error), phase: errorPhase, type: 'error' })
  }

  function broadcastFeedUnavailable(errorPhase: DesktopUpdatePhase): void {
    const language = store.read().language === 'en' ? 'en' : 'zh'

    broadcastUpdate({ message: FEED_UNAVAILABLE_MESSAGE[language], phase: errorPhase, type: 'error' })
  }

  // 自动检查可能早于生活空间开窗；新窗口订阅后读最新快照，补齐开窗前的状态。
  ipcMain.handle(IPC.invoke.updateGetState, () => latestEvent)

  // 始终注册更新通道，避免渲染层调用未注册的 handler 抛出 unhandled rejection。
  ipcMain.handle(IPC.invoke.updateCheck, async () => {
    if (!app.isPackaged) {
      broadcastUpdate({ type: 'none' })

      return
    }

    if (!feed.ensureFeedConfigured()) {
      broadcastFeedUnavailable('check')

      return
    }

    // checkForUpdates 失败时先发 'error' 事件再抛出；只由下方监听器上报，避免重复广播覆盖 404 映射。
    await electronUpdaterPkg.autoUpdater.checkForUpdates().catch(() => {})
  })

  ipcMain.handle(IPC.invoke.updateDownload, async () => {
    if (!app.isPackaged) {
      throw new Error('desktop updates are unavailable in development builds')
    }

    if (!feed.ensureFeedConfigured()) {
      broadcastFeedUnavailable('download')

      return
    }

    const { autoUpdater } = electronUpdaterPkg

    // 版本信息来自换号前的后端或尚未发现新版本：先按当前更新源检查，确有新版本再下载。
    if (!feed.isAvailableUpdateCurrent()) {
      const result = await autoUpdater.checkForUpdates().catch(() => null)

      if (!result?.isUpdateAvailable) {
        return
      }
    }

    // 重复点击复用进行中的下载；已缓存且校验通过的安装包直接复用，并再次触发 update-downloaded。失败先发 'error' 事件再抛出，同样只由监听器上报。
    phase = 'download'
    await autoUpdater.downloadUpdate().catch(() => {})
  })

  ipcMain.handle(IPC.invoke.updateInstall, event => {
    if (!app.isPackaged) {
      throw new Error('desktop updates are unavailable in development builds')
    }

    if (!isInstallSender(event.sender)) {
      throw new Error('update install is restricted to the living space window')
    }

    if (latestEvent?.type !== 'downloaded') {
      throw new Error('no verified update is ready to install')
    }

    phase = 'install'
    electronUpdaterPkg.autoUpdater.quitAndInstall(true, true)
  })

  if (!app.isPackaged) {
    return
  }

  const { autoUpdater } = electronUpdaterPkg
  autoUpdater.logger = log

  // 更新退出确实开始时置退出标志：原生 quitAndInstall 先关窗、后触发 before-quit，精灵窗的关闭拦截须提前放行；Windows 由 electron-updater 在退出前补发同名事件，安装器未启动时不发出。
  electronAutoUpdater.on('before-quit-for-update', markQuitting)

  autoUpdater.on('checking-for-update', () => {
    phase = 'check'
    broadcastUpdate({ type: 'checking' })
  })
  autoUpdater.on('update-available', info => broadcastUpdate({ info: toDesktopUpdateInfo(info), type: 'available' }))
  autoUpdater.on('update-not-available', info => broadcastUpdate({ info: toDesktopUpdateInfo(info), type: 'none' }))
  autoUpdater.on(
    'download-progress',
    // 契约只承诺三个字段，剔除 bytesPerSecond / delta 等原始结构。
    (raw: ProgressInfo) =>
      broadcastUpdate({
        progress: { percent: raw.percent, total: raw.total, transferred: raw.transferred },
        type: 'progress'
      })
  )
  // 安装包就绪后先预取并校验 Runner 资产，成功才进入可重启状态；失败按下载阶段上报，重试即再次下载。
  autoUpdater.on('update-downloaded', downloaded => {
    const info = toDesktopUpdateInfo(downloaded)

    broadcastUpdate({ info, type: 'preparing' })
    void enqueuePrepare(() =>
      feed.prefetchRunnerAssets(info.version).then(
        () => broadcastUpdate({ info, type: 'downloaded' }),
        (error: unknown) => {
          log.warn('runner prefetch failed:', errorMessage(error))
          broadcastError('download', error)
        }
      )
    )
  })
  autoUpdater.on('error', (err: unknown) => {
    const message = errorMessage(err)

    // 后端尚无发布版本时 latest.yml 返回 404，视为已是最新；latest-mac.yml 的 404 不在此列，照常上报。
    if (phase === 'check' && message.includes('404') && message.includes('latest.yml')) {
      broadcastUpdate({ info: undefined, type: 'none' })
    } else {
      broadcastError(phase, err)
    }
  })
}
