import type { DesktopUpdatePhase } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'
import type React from 'react'

import { BrandMark } from '@/shared/components/brand-mark'
import { RefreshCw } from '@/shared/lib/icons'
import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { BTN_PRIMARY, BTN_SUBTLE, SettingCard, SettingsSectionIntro, Spinner } from '@/shared/panel'
import { $updateStatus, setUpdateStatus, type UpdateStatus } from '@/shared/store/update'
import { $desktopVersion, refreshDesktopVersion } from '@/shared/store/version'
import { type Dictionary, useStrings } from '@/shared/strings'

type AboutStrings = Dictionary['settings']['about']

// 请求被主进程拒绝（如状态已变化）时不会收到更新事件，直接呈现为对应阶段的失败。
function requestUpdate(phase: DesktopUpdatePhase, request: () => Promise<void>): void {
  request().catch((error: unknown) => {
    log.warn('about', `update ${phase} request failed`, error)
    setUpdateStatus({ message: unwrapIpcErrorMessage(error), phase, status: 'error' })
  })
}

function statusLineFor(update: UpdateStatus, a: AboutStrings, appVersion: string | undefined): string {
  switch (update.status) {
    case 'checking':
      return a.checking

    case 'available':
      return a.updateAvailable(update.version)

    case 'downloading':
      return a.downloading(Math.floor(update.percent))

    case 'preparing':
      return a.preparing

    case 'downloaded':
      return a.updateDownloaded(update.version)

    case 'error':
      return { check: a.checkError, download: a.downloadError, install: a.installError }[update.phase](update.message)

    case 'none':
      return appVersion ? a.upToDateWithVersion(appVersion) : a.upToDate

    case 'idle':
      return a.upToDate
  }
}

export function AboutPage(): React.JSX.Element {
  const dict = useStrings()
  const a = dict.settings.about
  const nav = dict.settings.nav
  const version = useStore($desktopVersion)
  const updateStatus = useStore($updateStatus)

  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

  // 结果经 `$updateStatus` 呈现在下方 statusLine，无需独立对话框。
  const onCheckClick = useCallback(() => requestUpdate('check', window.spiritagent.update.check), [])
  const onDownloadClick = useCallback(() => requestUpdate('download', window.spiritagent.update.download), [])
  const onRestartClick = useCallback(() => requestUpdate('install', window.spiritagent.update.install), [])

  const { status } = updateStatus
  const isChecking = status === 'checking'
  // 下载、准备或已就绪时不再检查，避免新的检查结果覆盖进行中的更新。
  const checkDisabled = isChecking || status === 'downloading' || status === 'preparing' || status === 'downloaded'
  // 下载或安装失败时重新下载：已缓存的安装包直接复用，重新准备本机组件。
  const canDownload = status === 'available' || (updateStatus.status === 'error' && updateStatus.phase !== 'check')

  return (
    <div className="space-y-4">
      <SettingsSectionIntro hint={a.intro} title={nav.about} />
      <SettingCard className="px-6 py-8" divided={false}>
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="relative grid place-items-center">
            {/* 静态暖色光晕，呼应伙伴蛋的琥珀光（egg-stage.tsx）；设置页不做呼吸动画。 */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute z-0 size-32 rounded-full"
              style={{
                background: 'radial-gradient(closest-side, rgba(255,209,102,0.28), transparent 70%)',
                filter: 'blur(12px)'
              }}
            />
            <BrandMark className="relative z-10 size-16" />
          </div>
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-strong">{a.heading(dict.brand.fullName)}</h2>
            <p className="mt-1 text-xs text-faint">
              {version?.appVersion ? a.version(version.appVersion) : a.versionUnavailable}
            </p>
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-sm flex-col items-center gap-2 pt-4">
          <p aria-live="polite" className="text-center text-xs text-faint">
            {statusLineFor(updateStatus, a, version?.appVersion)}
          </p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            {canDownload && (
              <button className={BTN_PRIMARY} onClick={onDownloadClick} type="button">
                {status === 'error' ? a.retryDownload : a.downloadUpdate}
              </button>
            )}
            {status === 'downloaded' && (
              <button className={BTN_PRIMARY} onClick={onRestartClick} type="button">
                {a.restartNow}
              </button>
            )}
            <button className={BTN_SUBTLE} disabled={checkDisabled} onClick={onCheckClick} type="button">
              {isChecking ? <Spinner className="size-3.5" /> : <RefreshCw className="size-3.5" />}
              {a.checkForUpdates}
            </button>
          </div>
        </div>
      </SettingCard>
    </div>
  )
}
