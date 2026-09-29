import fs from 'node:fs'
import path from 'node:path'

import type { App } from 'electron'
import log from 'electron-log/main'
// CJS 包没有 named export，使用处从 default import 解构 autoUpdater。
import electronUpdaterPkg from 'electron-updater'

import type { BackendSessionLike } from '../shared/backend-port'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage } from '../shared/utils'

const UPDATE_INITIAL_CHECK_DELAY_MS = 30_000

type RunnerUpdaterLog = (level: string, message: string, ...args: unknown[]) => void

interface RunnerUpdaterPort {
  installPending: (appVersion: string) => Promise<{ error?: string; noop?: boolean; ok: boolean }>
  prefetchRunnerAssets: (options: {
    publicKeyPath: null | string
    updateBaseUrl: string
    version: string
  }) => Promise<void>
}

/** 仅更新器需要的会话/桥视图；结构对齐 RunnerUpdaterDeps，不 import runner。 */
interface RuntimeForUpdater {
  ensureBackendSession?: () => BackendSessionLike | null | undefined
  getRunnerBridge?: () => null | {
    getStatus: () => { phase: string }
    start: (options: { backendSession?: BackendSessionLike | null; readyTimeoutMs?: number }) => Promise<unknown>
    stop: (options: { reason: string }) => Promise<unknown>
  }
  spiritagentHome: string
}

interface AutoUpdaterOptions {
  app: Pick<App, 'getPath' | 'getVersion' | 'isPackaged'>
  runtime: RuntimeForUpdater
  /** 由 entry 注入，切断 lifecycle→runner 实现导入。 */
  createRunnerUpdater: (deps: {
    fetchImpl: typeof globalThis.fetch
    log: RunnerUpdaterLog
    runtime: RuntimeForUpdater
  }) => RunnerUpdaterPort
  fetchImpl: typeof globalThis.fetch
  spiritagentHome: null | string
}

// Runner 更新器的日志与桌面更新同落 electron-log。
const logRunnerUpdater: RunnerUpdaterLog = (level, message, ...args) => {
  if (level === 'error') {
    log.error(message, ...args)
  } else if (level === 'warn') {
    log.warn(message, ...args)
  } else {
    log.info(message, ...args)
  }
}

export function createAutoUpdater({
  app,
  runtime,
  createRunnerUpdater,
  fetchImpl,
  spiritagentHome
}: AutoUpdaterOptions) {
  let singleton: null | RunnerUpdaterPort = null
  let feedUrl: null | string = null
  // electron-updater 按最近一次发现新版本的检查结果下载；记下那次检查的更新源，Runner 预取与之同源。
  let availableFeedUrl: null | string = null

  function getRunnerUpdater(): RunnerUpdaterPort {
    if (singleton) {
      return singleton
    }

    singleton = createRunnerUpdater({ fetchImpl, log: logRunnerUpdater, runtime })

    return singleton
  }

  // electron-builder 的 extraResources 把验签公钥放在 resources 根目录；更新器只在打包构建运行。
  function getBundledPublicKeyPath(): null | string {
    const candidate = path.join(process.resourcesPath, 'update.pub')

    return fs.existsSync(candidate) ? candidate : null
  }

  // 更新源来自激活或换号时保存的后端地址；地址变化后在下一次检查或下载前重新配置，无需重启。
  function ensureFeedConfigured(): boolean {
    if (!app.isPackaged) {
      return false
    }

    const baseUrl = resolveNormalizedBackendUrl(spiritagentHome)

    if (!baseUrl) {
      return false
    }

    const url = `${baseUrl}/api/update`

    if (url !== feedUrl) {
      electronUpdaterPkg.autoUpdater.setFeedURL({ provider: 'generic', url })
      feedUrl = url
    }

    return true
  }

  /** 待下载的版本信息是否来自当前更新源；换后端或尚未发现新版本时须先重新检查。 */
  function isAvailableUpdateCurrent(): boolean {
    return feedUrl !== null && availableFeedUrl === feedUrl
  }

  // 桌面安装包下载后预取并校验同源同版本的 Runner 资产，写入待装标记，由同版本的新桌面进程启动时安装。
  async function prefetchRunnerAssets(version: string): Promise<void> {
    if (!availableFeedUrl) {
      throw new Error('update feed is not configured')
    }

    const publicKeyPath = getBundledPublicKeyPath()

    if (!publicKeyPath) {
      throw new Error('update.pub not found in resources; runner assets cannot be verified')
    }

    await getRunnerUpdater().prefetchRunnerAssets({ publicKeyPath, updateBaseUrl: availableFeedUrl, version })
  }

  // 启动时安装上个进程预取的 Runner 资产；失败只记日志，不阻断启动。
  async function installPendingRunnerUpdate(): Promise<void> {
    try {
      const result = await getRunnerUpdater().installPending(app.getVersion())

      if (!result.ok) {
        log.warn('runner installPending failed:', result.error)
      }
    } catch (err) {
      log.warn('runner installPending failed:', errorMessage(err))
    }
  }

  function setup(): void {
    if (!app.isPackaged) {
      return
    }

    const { autoUpdater } = electronUpdaterPkg

    autoUpdater.autoDownload = false
    autoUpdater.autoInstallOnAppQuit = false
    autoUpdater.logger = log
    autoUpdater.on('update-available', () => {
      availableFeedUrl = feedUrl
    })

    if (!ensureFeedConfigured()) {
      log.info('no backend URL configured; update feed is configured on the first check after activation')

      return
    }

    const timer = setTimeout(() => {
      autoUpdater.checkForUpdates().catch((error: unknown) => {
        const msg = errorMessage(error)
        log.warn('initial update check failed:', msg)
      })
    }, UPDATE_INITIAL_CHECK_DELAY_MS)

    if (typeof timer.unref === 'function') {
      timer.unref()
    }
  }

  return { ensureFeedConfigured, installPendingRunnerUpdate, isAvailableUpdateCurrent, prefetchRunnerAssets, setup }
}
