import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'
import type React from 'react'

import { BrandMark } from '@/shared/components/brand-mark'
import { RefreshCw } from '@/shared/lib/icons'
import { BTN_SUBTLE, SettingCard, SettingsSectionIntro, Spinner } from '@/shared/panel'
import { $updateStatus, selectTargetVersion } from '@/shared/store/update'
import { $desktopVersion, refreshDesktopVersion } from '@/shared/store/version'
import { useStrings } from '@/shared/strings'

export function AboutPage(): React.JSX.Element {
  const dict = useStrings()
  const a = dict.settings.about
  const nav = dict.settings.nav
  const version = useStore($desktopVersion)
  const updateStatus = useStore($updateStatus)

  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

  const onCheckClick = useCallback(() => {
    // 触发手动检查；下方 statusLine 随 `$updateStatus` 更新，无需独立对话框。
    void window.spiritagent?.update?.check?.()
  }, [])

  const isChecking = updateStatus.status === 'checking'
  const isAvailable = updateStatus.status === 'available' || updateStatus.status === 'downloading'
  const isDownloaded = updateStatus.status === 'downloaded'

  let statusLine: string = a.upToDate

  if (isChecking) {
    statusLine = a.checking
  } else if (isDownloaded) {
    statusLine = a.updateDownloaded(selectTargetVersion(updateStatus))
  } else if (isAvailable) {
    statusLine = a.updateAvailable(selectTargetVersion(updateStatus))
  } else if (updateStatus.status === 'none' && version?.appVersion) {
    statusLine = a.upToDateWithVersion(version.appVersion)
  } else if (updateStatus.status === 'error') {
    statusLine = a.updateError(updateStatus.message)
  }

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
            {statusLine}
          </p>
          <button className={BTN_SUBTLE} disabled={isChecking} onClick={onCheckClick} type="button">
            {isChecking ? <Spinner className="size-3.5" /> : <RefreshCw className="size-3.5" />}
            {a.checkForUpdates}
          </button>
        </div>
      </SettingCard>
    </div>
  )
}
