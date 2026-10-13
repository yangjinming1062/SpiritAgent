import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowRight, Check } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { PanelSelect } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type PresentationState = Awaited<ReturnType<DesktopBridge['presentation']['getState']>>
type Accounts = Awaited<ReturnType<DesktopBridge['desktop']['accounts']>>

const ACCOUNT_SUBMENU_WIDTH = 224
const ACCOUNT_SUBMENU_GAP = 8

type AccountSubmenuSide = 'below' | 'left' | 'right'
type AccountSubmenu = 'remove' | 'switch'

function selectableAccountIndexes(accounts: Accounts | null, submenu: AccountSubmenu): number[] {
  return (accounts ?? []).flatMap((account, index) => (submenu === 'switch' && account.active ? [] : [index]))
}

export function DesktopSettings({
  onSettings,
  presentation
}: {
  onSettings: () => void
  presentation: PresentationState | null
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
            void shellApi()
              .presentation.setDisplay(Number(value))
              .catch(error => notifyError(error, t.display))
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
          void shellApi()
            .presentation.setMode('window')
            .catch(error => notifyError(error, t.windowMode))
        }
        type="button"
      >
        {t.windowMode}
      </button>
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

export function DesktopAccounts({ onClose }: { onClose: () => void }): React.JSX.Element {
  const t = useDesktopStrings()
  const [accounts, setAccounts] = useState<Accounts | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [submenu, setSubmenu] = useState<AccountSubmenu | null>(null)
  const [submenuSide, setSubmenuSide] = useState<AccountSubmenuSide>('right')
  const beginAsync = useAsyncGuard()
  const menuRef = useRef<HTMLDivElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const triggerRefs = useRef<Record<AccountSubmenu, HTMLButtonElement | null>>({ remove: null, switch: null })
  const accountOptionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const focusOptionOnOpen = useRef(false)
  const focusDirectionOnOpen = useRef<'first' | 'last'>('first')
  const suppressTriggerOpen = useRef<AccountSubmenu | null>(null)
  useInteractiveRegion('desktop-accounts-popup', popupRef)

  const updateSubmenuSide = (): void => {
    const popup = menuRef.current

    if (!popup) {
      return
    }

    const rect = popup.getBoundingClientRect()
    const rightSpace = window.innerWidth - rect.right
    const leftSpace = rect.left

    setSubmenuSide(
      rightSpace >= ACCOUNT_SUBMENU_WIDTH + ACCOUNT_SUBMENU_GAP
        ? 'right'
        : leftSpace >= ACCOUNT_SUBMENU_WIDTH + ACCOUNT_SUBMENU_GAP
          ? 'left'
          : 'below'
    )
  }

  useLayoutEffect(() => {
    if (!submenu) {
      return
    }

    updateSubmenuSide()
    window.addEventListener('resize', updateSubmenuSide)

    return () => window.removeEventListener('resize', updateSubmenuSide)
  }, [submenu])

  const closeSubmenu = (): void => {
    focusOptionOnOpen.current = false
    focusDirectionOnOpen.current = 'first'
    setSubmenu(null)
  }

  const focusAccountOption = useCallback(
    (mode: AccountSubmenu, direction: 'first' | 'last' = 'first'): void => {
      const selectable = selectableAccountIndexes(accounts, mode)

      const index = direction === 'last' ? selectable.at(-1) : selectable[0]

      if (index !== undefined) {
        accountOptionRefs.current[index]?.focus()
      }
    },
    [accounts]
  )

  const openSubmenu = (mode: AccountSubmenu, focusOption = false, direction: 'first' | 'last' = 'first'): void => {
    if (busy) {
      return
    }

    if (focusOption) {
      if (submenu === mode && accounts) {
        focusOptionOnOpen.current = false
        focusAccountOption(mode, direction)
      } else {
        focusOptionOnOpen.current = true
        focusDirectionOnOpen.current = direction
      }
    } else {
      focusOptionOnOpen.current = false
    }

    setSubmenu(mode)
  }

  useEffect(() => {
    if (!submenu || !focusOptionOnOpen.current || !accounts) {
      return
    }

    focusOptionOnOpen.current = false
    focusAccountOption(submenu, focusDirectionOnOpen.current)
  }, [accounts, focusAccountOption, submenu])

  useEffect(() => {
    let disposed = false
    setAccounts(null)
    setFailed(false)

    void shellApi()
      .desktop.accounts()
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
    closeSubmenu()
    onClose()

    try {
      await action()
    } catch (error) {
      notifyError(error, t.accounts)
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const accountCounts = new Map<string, number>()

  for (const account of accounts ?? []) {
    accountCounts.set(account.username, (accountCounts.get(account.username) ?? 0) + 1)
  }

  const currentAccount = accounts?.find(account => account.active)

  const hasDuplicateName = (account: Accounts[number]): boolean => (accountCounts.get(account.username) ?? 0) > 1

  const accountDetail = (account: Accounts[number]): string | null =>
    hasDuplicateName(account) ? account.baseUrl : null

  const handlePopupBlur = (event: React.FocusEvent<HTMLDivElement>): void => {
    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
      closeSubmenu()
    }
  }

  const handleTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, mode: AccountSubmenu): void => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openSubmenu(mode, true)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      openSubmenu(mode, true, 'last')
    } else if ((event.key === 'ArrowLeft' || event.key === 'Escape') && submenu === mode) {
      event.preventDefault()
      event.stopPropagation()
      closeSubmenu()
    }
  }

  const handleAccountOptionKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    if (!submenu) {
      return
    }

    const selectable = selectableAccountIndexes(accounts, submenu)

    const current = selectable.indexOf(index)

    if (selectable.length === 0) {
      return
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const delta = event.key === 'ArrowDown' ? 1 : -1
      const next = selectable[(current + delta + selectable.length) % selectable.length]

      if (next !== undefined) {
        accountOptionRefs.current[next]?.focus()
      }
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const next = event.key === 'Home' ? selectable[0] : selectable.at(-1)

      if (next !== undefined) {
        accountOptionRefs.current[next]?.focus()
      }
    } else if (event.key === 'Escape' || event.key === 'ArrowLeft') {
      event.preventDefault()
      event.stopPropagation()
      suppressTriggerOpen.current = submenu
      closeSubmenu()
      triggerRefs.current[submenu]?.focus()
    }
  }

  const submenuTrigger = (mode: AccountSubmenu, label: string): React.JSX.Element => (
    <div className={styles.accountSwitch} onPointerEnter={() => openSubmenu(mode)}>
      <button
        aria-expanded={submenu === mode}
        aria-haspopup="menu"
        className={styles.menuAction}
        data-destructive={mode === 'remove'}
        disabled={busy}
        onClick={() => openSubmenu(mode)}
        onFocus={() => {
          const suppressed = suppressTriggerOpen.current === mode
          suppressTriggerOpen.current = null

          if (!suppressed) {
            openSubmenu(mode)
          }
        }}
        onKeyDown={event => handleTriggerKeyDown(event, mode)}
        ref={element => {
          triggerRefs.current[mode] = element
        }}
        type="button"
      >
        <span>{label}</span>
        <ArrowRight aria-hidden="true" size={14} />
      </button>
    </div>
  )

  const submenuLabel = submenu === 'remove' ? t.removableAccounts : t.accountList

  return (
    <div
      className={styles.accountPopup}
      data-side={submenuSide}
      onBlur={handlePopupBlur}
      onPointerLeave={closeSubmenu}
      ref={popupRef}
    >
      <div className={`${styles.menuContent} ${styles.accountMenu}`} ref={menuRef}>
        <div className={styles.accountSummary}>
          <span className={styles.accountSummaryLabel}>{t.currentAccount}</span>
          {accounts === null && !failed && <span role="status">{t.accountLoading}</span>}
          {failed && <span role="status">{t.accountLoadFailed}</span>}
          {currentAccount && (
            <span className={styles.accountSummaryName}>
              <span>{currentAccount.username}</span>
              {accountDetail(currentAccount) && <small>{accountDetail(currentAccount)}</small>}
            </span>
          )}
        </div>
        {submenuTrigger('switch', t.switchAccount)}
        {failed && (
          <button
            className={styles.menuAction}
            onClick={() => setRetry(value => value + 1)}
            onFocus={closeSubmenu}
            onPointerEnter={closeSubmenu}
            tabIndex={submenu ? -1 : 0}
            type="button"
          >
            {t.retry}
          </button>
        )}
        <button
          className={styles.menuAction}
          disabled={busy}
          onClick={() => void run(() => shellApi().desktop.addAccount())}
          onFocus={closeSubmenu}
          onPointerEnter={closeSubmenu}
          tabIndex={submenu ? -1 : 0}
          type="button"
        >
          {t.addAccount}
        </button>
        {submenuTrigger('remove', t.removeAccount)}
      </div>
      {submenu && (
        <div aria-label={submenuLabel} className={styles.accountSubmenu} role="menu">
          <div aria-hidden="true" className={styles.accountSubmenuTitle}>
            {submenuLabel}
          </div>
          {accounts === null && !failed && <span className={styles.menuStatus}>{t.accountLoading}</span>}
          {failed && <span className={styles.menuStatus}>{t.accountLoadFailed}</span>}
          {accounts?.map((account, index) => (
            <button
              aria-checked={submenu === 'switch' ? account.active : undefined}
              aria-current={submenu === 'remove' && account.active ? 'true' : undefined}
              className={styles.accountOption}
              data-destructive={submenu === 'remove'}
              disabled={busy || (submenu === 'switch' && account.active)}
              key={account.id}
              onClick={() =>
                void run(() =>
                  submenu === 'remove'
                    ? shellApi().desktop.removeAccount(account.id)
                    : shellApi().desktop.switchAccount(account.id)
                )
              }
              onKeyDown={event => handleAccountOptionKeyDown(event, index)}
              ref={element => {
                accountOptionRefs.current[index] = element
              }}
              role={submenu === 'remove' ? 'menuitem' : 'menuitemradio'}
              tabIndex={index === accounts.findIndex(item => submenu === 'remove' || !item.active) ? 0 : -1}
              title={account.baseUrl}
              type="button"
            >
              <span className={styles.accountOptionText}>
                <span>{account.username}</span>
                {accountDetail(account) && <small>{accountDetail(account)}</small>}
              </span>
              {account.active && <Check aria-hidden="true" size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
