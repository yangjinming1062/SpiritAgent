import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { $defaultScale, resetToHomePosition, setDefaultScale, syncDefaultScale } from '@/modules/character'
import { BTN_SUBTLE, PanelSelect, SettingCard, SettingRow, SettingsSectionIntro, Slider, Toggle } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceCompanion, setSurfaceCompanion } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'
import { SPRITE_SCALE_LIMITS } from '@ipc/contracts'

export function CompanionPage(): React.JSX.Element {
  const t = useStrings().settings.companion
  const scale = useStore($defaultScale)
  const presentation = useStore($presentation)
  const companion = useStore($surfaceCompanion)
  const controls = useStrings().common.companionControl

  useEffect(() => shellApi().sprite.onDefaultScaleChanged(syncDefaultScale), [])

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.hint} title={t.heading} />
      <SettingCard>
        <SettingRow label={controls.show}>
          <Toggle
            ariaLabel={controls.show}
            checked={companion.preference.enabled}
            onChange={enabled =>
              void setSurfaceCompanion({ ...companion.preference, enabled }).catch(error =>
                notifyError(error, controls.title)
              )
            }
          />
        </SettingRow>
        <SettingRow label={controls.title}>
          <PanelSelect
            ariaLabel={controls.title}
            onChange={side =>
              void setSurfaceCompanion({ ...companion.preference, side }).catch(error =>
                notifyError(error, controls.title)
              )
            }
            options={[
              { value: 'left', label: controls.left },
              { value: 'right', label: controls.right }
            ]}
            value={companion.preference.side}
            widthClass="w-48"
          />
        </SettingRow>
        <SettingRow description={t.companionSizeHint} label={t.companionSize}>
          <div className="flex w-48 items-center gap-3">
            <Slider
              ariaLabel={t.scaleAria}
              disabled={presentation.effectiveMode === 'desktop'}
              max={SPRITE_SCALE_LIMITS.max}
              min={SPRITE_SCALE_LIMITS.min}
              onChange={setDefaultScale}
              step={0.05}
              value={scale}
            />
            <span className="w-10 shrink-0 text-right font-mono text-xs text-body">
              {String(Number(scale.toFixed(2)))}×
            </span>
          </div>
        </SettingRow>
        <SettingRow description={t.resetPositionDesc} label={t.resetPosition}>
          <button className={BTN_SUBTLE} onClick={resetToHomePosition} type="button">
            {t.resetPosition}
          </button>
        </SettingRow>
      </SettingCard>
    </div>
  )
}
