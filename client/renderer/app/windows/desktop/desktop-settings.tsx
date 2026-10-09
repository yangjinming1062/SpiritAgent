import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { PanelSelect } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type PresentationState = Awaited<ReturnType<typeof window.spiritagent.presentation.getState>>
type Accounts = Awaited<ReturnType<typeof window.spiritagent.desktop.accounts>>

export function DesktopSettings({
  presentation,
  onSettings
}: {
  presentation: PresentationState | null
  onSettings: () => void
}): React.JSX.Element {
  const t = useDesktopStrings()
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
          disabled={!presentation?.displays.length}
          onChange={value =>
            void window.spiritagent.presentation.setDisplay(Number(value)).catch(error => notifyError(error, t.display))
          }
          options={
            presentation?.displays.map(display => ({
              value: String(display.id),
              label: `${display.label} · ${display.width} × ${display.height}`
            })) ?? []
          }
          value={String(presentation?.displayId ?? '')}
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
      {presentation?.compatibilityWarning && <span role="status">{presentation.compatibilityWarning}</span>}
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
  const menuRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-accounts-popup', menuRef)

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
    <div className={styles.menuContent} ref={menuRef}>
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
