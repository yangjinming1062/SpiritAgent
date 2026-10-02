import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { LivingSettings } from '@/app/features/living/settings/living-settings'
import { StationSettings } from '@/app/features/workbench/station-settings'
import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { notifyError } from '@/shared/store/notifications'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type PresentationState = Awaited<ReturnType<typeof window.spiritagent.presentation.getState>>
type Accounts = Awaited<ReturnType<typeof window.spiritagent.desktop.accounts>>

export function DesktopSettings({
  presentation,
  spriteVisible,
  onSpriteToggle,
  onSettings
}: {
  presentation: PresentationState | null
  spriteVisible: boolean
  onSpriteToggle: () => void
  onSettings: () => void
}): React.JSX.Element {
  const t = useDesktopStrings()
  const preferredTier = useStore($userPreferredTier)

  return (
    <div className={styles.menuContent}>
      <strong>{t.desktopSettings}</strong>
      <label className={styles.displaySelect}>
        {t.display}
        <select
          onChange={event =>
            void window.spiritagent.presentation
              .setDisplay(Number(event.target.value))
              .catch(error => notifyError(error, t.display))
          }
          value={presentation?.displayId ?? ''}
        >
          {presentation?.displays.map(display => (
            <option key={display.id} value={display.id}>
              {display.label} · {display.width} × {display.height}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.displaySelect}>
        {t.disturbance}
        <select
          onChange={event => {
            const value = event.target.value

            if (value === 'still' || value === 'normal' || value === 'autonomous') {
              setDisturbanceTier(value)
            }
          }}
          value={preferredTier}
        >
          <option value="still">{t.still}</option>
          <option value="normal">{t.normal}</option>
          <option value="autonomous">{t.autonomous}</option>
        </select>
      </label>
      <button onClick={onSpriteToggle} type="button">
        {spriteVisible ? t.hideCompanion : t.showCompanion}
      </button>
      <button onClick={onSettings} type="button">
        {t.settings}
      </button>
      <button
        onClick={() =>
          void window.spiritagent.presentation.setMode('window').catch(error => notifyError(error, t.windowMode))
        }
        type="button"
      >
        {t.windowMode}
      </button>
      <button
        onClick={() => void window.spiritagent.desktop.quit().catch(error => notifyError(error, t.quit))}
        type="button"
      >
        {t.quit}
      </button>
    </div>
  )
}

export function DesktopAccounts(): React.JSX.Element {
  const t = useDesktopStrings()
  const [accounts, setAccounts] = useState<Accounts | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    let disposed = false
    void window.spiritagent.desktop
      .accounts()
      .then(value => {
        if (!disposed) {
          setAccounts(value)
          setFailed(false)
        }
      })
      .catch(error => {
        if (!disposed) {
          setFailed(true)
          notifyError(error, t.accounts)
        }
      })

    return () => {
      disposed = true
    }
  }, [retry, t.accounts])

  const run = async (action: () => Promise<unknown>): Promise<void> => {
    const isLive = beginAsync()
    setBusy(true)

    try {
      await action()
    } catch (error) {
      if (isLive()) {
        notifyError(error, t.accounts)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return (
    <div className={styles.menuContent}>
      <strong>{t.accounts}</strong>
      {accounts === null && !failed && <span>{t.accountLoading}</span>}
      {failed && (
        <button onClick={() => setRetry(value => value + 1)} type="button">
          {t.retry}
        </button>
      )}
      {accounts?.map(account => (
        <button
          disabled={busy || account.active}
          key={account.id}
          onClick={() => void run(() => window.spiritagent.desktop.switchAccount(account.id))}
          title={account.baseUrl}
          type="button"
        >
          <span>{account.username}</span>
          {account.active && <span>✓</span>}
        </button>
      ))}
      <button disabled={busy} onClick={() => void run(() => window.spiritagent.desktop.addAccount())} type="button">
        {t.addAccount}
      </button>
      <button
        disabled={busy}
        onClick={() => void run(() => window.spiritagent.presentation.setMode('window'))}
        type="button"
      >
        {t.windowMode}
      </button>
      <button disabled={busy} onClick={() => void run(() => window.spiritagent.desktop.quit())} type="button">
        {t.quit}
      </button>
    </div>
  )
}

export function DesktopPreferences({
  tab,
  onTabChange
}: {
  tab: 'living' | 'station'
  onTabChange: (tab: 'living' | 'station') => void
}): React.JSX.Element {
  const t = useDesktopStrings()

  return (
    <div className={styles.preferences}>
      <div aria-label={t.settings} className={styles.preferencesTabs} role="tablist">
        <button aria-selected={tab === 'living'} onClick={() => onTabChange('living')} role="tab" type="button">
          {t.livingSettings}
        </button>
        <button aria-selected={tab === 'station'} onClick={() => onTabChange('station')} role="tab" type="button">
          {t.station}
        </button>
      </div>
      {tab === 'living' ? <LivingSettings /> : <StationSettings />}
    </div>
  )
}
