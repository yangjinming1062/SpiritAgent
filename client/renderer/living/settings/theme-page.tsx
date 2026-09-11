import { useStore } from '@nanostores/react'
import type React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { SECTION_TITLE, SettingCard, SettingRow, SettingsSectionIntro } from '@/shared/panel'
import { $theme, setUiTheme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'
import { THEMES } from '@/shared/theme/registry'

export function ThemePage(): React.JSX.Element {
  const dict = useStrings()
  const appearance = dict.settings.appearance
  const themeText = dict.settings.theme
  const active = useStore($theme)

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={appearance.hint} title={appearance.heading} />

      <section>
        <p className={cn(SECTION_TITLE, 'mb-2.5')}>{themeText.themesHeading}</p>
        <div aria-label={themeText.themesAriaLabel} className="grid grid-cols-1 gap-3 sm:grid-cols-2" role="radiogroup">
          {THEMES.map(theme => {
            const isActive = theme.id === active

            return (
              <button
                aria-checked={isActive}
                className={cn('theme-tile select-none text-left', isActive && 'is-active')}
                key={theme.id}
                onClick={() => {
                  triggerHaptic('open')
                  setUiTheme(theme.id)
                }}
                role="radio"
                type="button"
              >
                <div className="theme-preview" data-preview={theme.id}>
                  <div className="theme-preview-body">
                    <div className="theme-preview-rail" />
                    <div className="theme-preview-stage">
                      <div className="theme-preview-bubble" />
                    </div>
                  </div>
                  {isActive ? (
                    <span className="theme-preview-check">
                      <Check className="size-3 stroke-[2.5]" />
                    </span>
                  ) : null}
                </div>

                <div className="mt-2.5 flex items-center justify-between gap-2">
                  <span
                    className={cn('text-[13px] font-medium transition-colors', isActive ? 'text-strong' : 'text-body')}
                  >
                    {theme.label}
                  </span>
                  {isActive && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-[10px] font-medium text-accent">
                      <Check className="size-2.5 stroke-[2.5]" />
                      <span>{themeText.activeBadge}</span>
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{theme.description}</p>
              </button>
            )
          })}
        </div>
      </section>

      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{themeText.materialHeading}</p>
        <SettingCard>
          <SettingRow description={themeText.materialTransparentDesc} label={themeText.materialTransparent}>
            <span className="text-[11px] text-faint">{themeText.materialTransparentDayNight}</span>
          </SettingRow>
          <SettingRow description={themeText.materialSolidDesc} label={themeText.materialSolid}>
            <span className="text-[11px] text-faint">{themeText.materialSolidDayNight}</span>
          </SettingRow>
        </SettingCard>
      </section>
    </div>
  )
}
