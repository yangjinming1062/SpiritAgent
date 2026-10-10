import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { $defaultScale, resetToHomePosition, setDefaultScale, syncDefaultScale } from '@/modules/character'
import { BTN_SUBTLE, SettingCard, SettingRow, SettingsSectionIntro, Slider } from '@/shared/panel'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import { SPRITE_SCALE_LIMITS } from '@ipc/contracts'

export function CompanionPage(): React.JSX.Element {
  const t = useStrings().settings.companion
  const scale = useStore($defaultScale)
  const presentation = useStore($presentation)

  useEffect(() => window.spiritagent.sprite.onDefaultScaleChanged(syncDefaultScale), [])

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.hint} title={t.heading} />
      <SettingCard>
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
