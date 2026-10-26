import type React from 'react'
import { useState } from 'react'

import { AccessibilityPermissionButton } from '@/app/components/accessibility-permission-button'
import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { presentationDisplayOptions } from '@/shared/lib/presentation-displays'
import { BTN_SUBTLE, HINT_TEXT, PanelSelect, SECTION_TITLE, SettingCard, SettingRow } from '@/shared/panel'
import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
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
    <section className="space-y-3">
      <h3 className={SECTION_TITLE}>{strings.settings.presentation.title}</h3>
      <p className={HINT_TEXT}>{strings.settings.presentation.hint}</p>
      <SettingCard className="overflow-visible">
        <SettingRow
          description={
            state.supported
              ? strings.settings.presentation.modeDescription
              : state.unsupportedReason || strings.settings.presentation.modeDescriptionUnsupported
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
        {state.supported && desktopPlatform() === 'darwin' && (
          <SettingRow
            description={
              state.accessibility.granted
                ? strings.desktopShell.accessibilityGranted
                : strings.desktopShell.accessibilityDescription
            }
            label={strings.desktopShell.accessibilityTitle}
          >
            <AccessibilityPermissionButton className={BTN_SUBTLE} />
          </SettingRow>
        )}
      </SettingCard>
      {(state.switchBlockedReason || state.failureReason || state.compatibilityWarning) && (
        <p className="text-sm text-muted" role="status">
          {state.switchBlockedReason || state.failureReason || state.compatibilityWarning}
        </p>
      )}
    </section>
  )
}
