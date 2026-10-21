import type React from 'react'
import { useState } from 'react'

import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { presentationDisplayOptions } from '@/shared/lib/presentation-displays'
import { BTN_SUBTLE, PanelSelect, SettingCard, SettingRow, SettingsSectionIntro } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

export function PresentationPage(): React.JSX.Element {
  const strings = useStrings()
  const { state, busy, switchMode } = usePresentationModeSwitch()
  const [displayBusy, setDisplayBusy] = useState(false)
  const beginAsync = useAsyncGuard()

  const selectDisplay = async (value: string): Promise<void> => {
    if (!value) {
      return
    }

    const isLive = beginAsync()
    setDisplayBusy(true)

    try {
      await shellApi().presentation.setDisplay(Number(value))
    } catch (error) {
      if (isLive()) {
        notifyError(error, strings.settings.presentation.switchFailed)
      }
    } finally {
      if (isLive()) {
        setDisplayBusy(false)
      }
    }
  }

  return (
    <div className="space-y-8">
      <SettingsSectionIntro hint={strings.settings.presentation.hint} title={strings.settings.presentation.title} />
      <SettingCard className="overflow-visible">
        <SettingRow
          description={
            state.supported
              ? strings.settings.presentation.modeDescription
              : strings.settings.presentation.modeDescriptionUnsupported
          }
          label={strings.settings.presentation.displayModeLabel}
        >
          <button
            className={BTN_SUBTLE}
            disabled={busy || !state.supported}
            onClick={() => void switchMode(state.effectiveMode === 'desktop' ? 'window' : 'desktop')}
            title={state.switchBlockedReason ?? undefined}
            type="button"
          >
            {state.effectiveMode === 'desktop'
              ? strings.settings.presentation.switchToWindowMode
              : strings.settings.presentation.switchToDesktopMode}
          </button>
        </SettingRow>
        {state.supported && (
          <SettingRow
            description={strings.settings.presentation.displayDescription}
            label={strings.settings.presentation.displayLabel}
          >
            <PanelSelect
              ariaLabel={strings.settings.presentation.displayAria}
              disabled={displayBusy || busy || !state.displays.length}
              onChange={value => void selectDisplay(value)}
              options={presentationDisplayOptions(state, strings.desktopShell)}
              value={String(state.displayId ?? '')}
              widthClass="w-full sm:w-72"
            />
          </SettingRow>
        )}
      </SettingCard>
      {(state.switchBlockedReason || state.failureReason || state.compatibilityWarning) && (
        <p className="text-sm text-muted" role="status">
          {state.switchBlockedReason || state.failureReason || state.compatibilityWarning}
        </p>
      )}
    </div>
  )
}
