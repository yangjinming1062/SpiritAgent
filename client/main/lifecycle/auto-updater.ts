import fs from 'node:fs'
import path from 'node:path'

import type { App } from 'electron'
import log from 'electron-log/main'
// CJS 包没有 named export，使用处从 default import 解构 autoUpdater。
import electronUpdaterPkg from 'electron-updater'
import type { UpdateInfo } from 'electron-updater'

import type { BackendSessionLike } from '../shared/backend-port'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage } from '../shared/utils'

const UPDATE_INITIAL_CHECK_DELAY_MS = 30_000

type RunnerUpdaterLog = (level: string, message: string, ...args: unknown[]) => void

interface RunnerUpdaterPort {
  installPending: () => Promise<{ error?: string; noop?: boolean; ok: boolean }>
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
  let feedConfigured = false

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

  // 更新源来自激活时保存的后端地址；首次激活前没有地址，之后的检查再配置，无需重启。
  function ensureFeedConfigured(): boolean {
    if (feedConfigured) {
      return true
    }

    if (!app.isPackaged) {
      return false
    }

    const baseUrl = resolveNormalizedBackendUrl(spiritagentHome)

    if (!baseUrl) {
      return false
    }

    const { autoUpdater } = electronUpdaterPkg
    const updateBaseUrl = baseUrl + '/api/update'
    const publicKeyPath = getBundledPublicKeyPath()

    if (!publicKeyPath) {
      log.warn('update.pub not found in extraResources; runner signature verification will fail')
    }

    autoUpdater.setFeedURL({
      provider: 'generic',
      url: updateBaseUrl
    })
    feedConfigured = true

    autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
      log.info('desktop update downloaded; starting runner prefetch', info?.version)
      getRunnerUpdater()
        .prefetchRunnerAssets({
          publicKeyPath,
          updateBaseUrl,
          version: info?.version || app.getVersion()
        })
        .catch(err => {
          log.warn('runner prefetch failed:', errorMessage(err))
        })
    })

    return true
  }

  function setup(): void {
    if (!app.isPackaged) {
      return
    }

    const { autoUpdater } = electronUpdaterPkg

    autoUpdater.autoDownload = false
    autoUpdater.autoInstallOnAppQuit = false
    autoUpdater.logger = log

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

  return { ensureFeedConfigured, getRunnerUpdater, setup }
}
