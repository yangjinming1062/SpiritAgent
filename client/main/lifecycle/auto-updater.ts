import fs from 'node:fs'
import path from 'node:path'

import type { App, Net } from 'electron'
import log from 'electron-log/main'
import electronUpdaterPkg from 'electron-updater'
import type { UpdateInfo } from 'electron-updater'

import type { RunnerUpdaterDeps } from '../runner/updater'
import { RunnerUpdater } from '../runner/updater'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage } from '../shared/utils'

const UPDATE_INITIAL_CHECK_DELAY_MS = 30_000

interface AutoUpdaterOptions {
  app: Pick<App, 'getPath' | 'getVersion' | 'isPackaged'>
  appRoot: string
  bridgeDeps: RunnerUpdaterDeps['bridgeDeps']
  electronNet: Net
  spiritagentHome: null | string
}

export function createAutoUpdater({ app, appRoot, bridgeDeps, electronNet, spiritagentHome }: AutoUpdaterOptions) {
  let singleton: RunnerUpdater | null = null

  function getRunnerUpdater(): RunnerUpdater {
    if (singleton) {
      return singleton
    }

    singleton = new RunnerUpdater({
      bridgeDeps,
      fetchImpl: electronNet.fetch as unknown as typeof globalThis.fetch
    })

    return singleton
  }

  function getBundledPublicKeyPath(): null | string {
    try {
      const candidates = [
        path.join(process.resourcesPath || '', 'update.pub'),
        path.join(appRoot, 'update.pub'),
        path.join(import.meta.dirname, '..', 'update.pub'),
        path.join(import.meta.dirname, 'update.pub'),
        path.join(appRoot, '..', 'scripts', 'release-keys', 'update.pub'),
        path.resolve(appRoot, '../../scripts/release-keys/update.pub')
      ]

      return candidates.find(candidate => fs.existsSync(candidate)) || null
    } catch {
      return null
    }
  }

  function setup(): void {
    if (!app.isPackaged) {
      return
    }

    // 从 default import 解构；不要用顶层 named import（见 electronUpdaterPkg 注释）。
    const { autoUpdater } = electronUpdaterPkg

    autoUpdater.autoDownload = false
    autoUpdater.autoInstallOnAppQuit = false
    autoUpdater.logger = log

    const baseUrl = resolveNormalizedBackendUrl(spiritagentHome)

    if (!baseUrl) {
      log.info('no backend URL configured; auto-updater disabled until activation')

      return
    }

    const updateBaseUrl = baseUrl + '/api/update'
    const publicKeyPath = getBundledPublicKeyPath()

    if (!publicKeyPath) {
      log.warn('update.pub not found in extraResources; runner signature verification will fail')
    }

    autoUpdater.setFeedURL({
      provider: 'generic',
      url: updateBaseUrl
    })

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

  return { getRunnerUpdater, setup }
}
