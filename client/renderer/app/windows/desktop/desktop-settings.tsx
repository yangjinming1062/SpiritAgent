import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { PanelSelect } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

export function DesktopSettings({ onSettings }: { onSettings: () => void }): React.JSX.Element {
  const t = useDesktopStrings()
  const { busy, switchMode, state } = usePresentationModeSwitch()
  const preferredTier = useStore($userPreferredTier)
  const menuRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-settings-popup', menuRef)

  return (
    <div className={styles.menuContent} ref={menuRef}>
      <strong>{t.desktopSettings}</strong>
      <div className={styles.displaySelect}>
        <span>{t.display}</span>
        <PanelSelect
          ariaLabel={t.display}
          disabled={busy || !state.displays.length}
          onChange={value =>
            void shellApi()
              .presentation.setDisplay(Number(value))
              .catch(error => notifyError(error, t.display))
          }
          options={state.displays.map(display => ({
            value: String(display.id),
            label: `${display.label} · ${display.width} × ${display.height}`
          }))}
          value={String(state.displayId ?? '')}
          widthClass="w-full"
        />
      </div>
      <div className={styles.displaySelect}>
        <span>{t.disturbance}</span>
        <PanelSelect
          ariaLabel={t.disturbance}
          onChange={setDisturbanceTier}
          options={[
            { value: 'still', label: t.still },
            { value: 'normal', label: t.normal },
            { value: 'autonomous', label: t.autonomous }
          ]}
          value={preferredTier}
          widthClass="w-full"
        />
      </div>
      {state.compatibilityWarning && <span role="status">{state.compatibilityWarning}</span>}
      <button onClick={onSettings} type="button">
        {t.settings}
      </button>
      <button
        disabled={busy}
        onClick={() => void switchMode('window')}
        title={state.switchBlockedReason ?? t.windowMode}
        type="button"
      >
        {t.windowMode}
      </button>
      {state.switchBlockedReason && <span role="status">{state.switchBlockedReason}</span>}
      <button
        onClick={() =>
          void shellApi()
            .desktop.quit()
            .catch(error => notifyError(error, t.quit))
        }
        type="button"
      >
        {t.quit}
      </button>
    </div>
  )
}
