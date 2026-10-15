import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { BTN_SUBTLE, PanelSelect, SettingCard, SettingRow, SettingsSectionIntro } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'

export function PresentationPage(): React.JSX.Element {
  const english = useStore($locale) === 'en'
  const { state, busy, switchMode } = usePresentationModeSwitch()
  const [displayBusy, setDisplayBusy] = useState(false)
  const beginAsync = useAsyncGuard()

  const selectDisplay = async (value: string): Promise<void> => {
    const isLive = beginAsync()
    setDisplayBusy(true)

    try {
      await shellApi().presentation.setDisplay(Number(value))
    } catch (error) {
      if (isLive()) {
        notifyError(error, english ? 'Interactive display' : '交互显示器')
      }
    } finally {
      if (isLive()) {
        setDisplayBusy(false)
      }
    }
  }

  return (
    <div className="space-y-8">
      <SettingsSectionIntro
        hint={english ? 'Choose where your companion appears.' : '选择让伙伴出现在窗口旁，还是住进电脑桌面。'}
        title={english ? 'Where your companion appears' : '伙伴出现在哪里'}
      />
      <SettingCard>
        <SettingRow
          description={
            state.supported
              ? english
                ? 'Window mode uses a main window and floating companion. Desktop mode places your companion in the desktop background.'
                : '窗口模式通过主窗口和悬浮伙伴互动；桌面模式将伙伴呈现在桌面背景中。'
              : english
                ? 'Desktop mode is available on Windows.'
                : '桌面模式仅在 Windows 上可用。'
          }
          label={english ? 'Display mode' : '显示方式'}
        >
          <button
            className={BTN_SUBTLE}
            disabled={busy || !state.supported}
            onClick={() => void switchMode(state.effectiveMode === 'desktop' ? 'window' : 'desktop')}
            title={state.switchBlockedReason ?? undefined}
            type="button"
          >
            {state.effectiveMode === 'desktop'
              ? english
                ? 'Switch to window mode'
                : '切换到窗口模式'
              : english
                ? 'Switch to desktop mode'
                : '切换到桌面模式'}
          </button>
        </SettingRow>
        {state.supported && (
          <SettingRow
            description={
              english
                ? 'Desktop controls and background appear on this display.'
                : '桌面控制界面与背景显示在这块屏幕上。'
            }
            label={english ? 'Display' : '显示屏'}
          >
            <PanelSelect
              ariaLabel={english ? 'Interactive display' : '交互显示器'}
              disabled={displayBusy || busy || !state.displays.length}
              onChange={value => void selectDisplay(value)}
              options={state.displays.map(display => ({
                value: String(display.id),
                label: `${display.label} · ${display.width} × ${display.height}`
              }))}
              value={String(state.displayId ?? '')}
              widthClass="w-56"
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
