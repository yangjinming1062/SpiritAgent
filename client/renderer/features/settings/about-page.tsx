import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { $desktopVersion, refreshDesktopVersion } from '@/shared/store/version'
import { useStrings } from '@/shared/strings'
import { SettingCard, SettingsSectionIntro } from '@/ui'
import { BrandMark } from '@/ui/brand-mark'

export function AboutPage(): React.JSX.Element {
  const dict = useStrings()
  const a = dict.settings.about
  const nav = dict.settings.nav
  const version = useStore($desktopVersion)

  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

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
      </SettingCard>
    </div>
  )
}
