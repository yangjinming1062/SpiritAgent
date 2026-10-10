import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowRight, Check } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { PanelSelect } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type PresentationState = Awaited<ReturnType<typeof window.spiritagent.presentation.getState>>
type Accounts = Awaited<ReturnType<typeof window.spiritagent.desktop.accounts>>

const ACCOUNT_SUBMENU_WIDTH = 224
const ACCOUNT_SUBMENU_GAP = 8

type AccountSubmenuSide = 'below' | 'left' | 'right'

export function DesktopSettings({ presentation }: { presentation: PresentationState | null }): React.JSX.Element {
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

export function DesktopAccounts({ onClose }: { onClose: () => void }): React.JSX.Element {
  const t = useDesktopStrings()
  const [accounts, setAccounts] = useState<Accounts | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [switchOpen, setSwitchOpen] = useState(false)
  const [submenuSide, setSubmenuSide] = useState<AccountSubmenuSide>('right')
  const beginAsync = useAsyncGuard()
  const menuRef = useRef<HTMLDivElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const accountOptionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const focusOptionOnOpen = useRef(false)
  const focusDirectionOnOpen = useRef<'first' | 'last'>('first')
  const suppressTriggerOpen = useRef(false)
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
    if (!switchOpen) {
      return
    }

    updateSubmenuSide()
    window.addEventListener('resize', updateSubmenuSide)

    return () => window.removeEventListener('resize', updateSubmenuSide)
  }, [switchOpen])

  const closeSwitcher = (): void => {
    focusOptionOnOpen.current = false
    focusDirectionOnOpen.current = 'first'
    setSwitchOpen(false)
  }

  const focusAccountOption = useCallback(
    (direction: 'first' | 'last' = 'first'): void => {
      const selectable = (accounts ?? [])
        .map((account, index) => (account.active ? -1 : index))
        .filter(index => index >= 0)

      const index = direction === 'last' ? selectable.at(-1) : selectable[0]

      if (index !== undefined) {
        accountOptionRefs.current[index]?.focus()
      }
    },
    [accounts]
  )

  const openSwitcher = (focusOption = false, direction: 'first' | 'last' = 'first'): void => {
    if (focusOption) {
      if (switchOpen && accounts) {
        focusOptionOnOpen.current = false
        focusAccountOption(direction)
      } else {
        focusOptionOnOpen.current = true
        focusDirectionOnOpen.current = direction
      }
    }

    setSwitchOpen(true)
  }

  useEffect(() => {
    if (!switchOpen || !focusOptionOnOpen.current || !accounts) {
      return
    }

    focusOptionOnOpen.current = false
    focusAccountOption(focusDirectionOnOpen.current)
  }, [accounts, focusAccountOption, switchOpen])

  useEffect(() => {
    let disposed = false
    setAccounts(null)
    setFailed(false)

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
    closeSwitcher()
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
      closeSwitcher()
    }
  }

  const handleSwitchKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openSwitcher(true)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      openSwitcher(true, 'last')
    } else if (event.key === 'ArrowLeft' && switchOpen) {
      event.preventDefault()
      closeSwitcher()
    }
  }

  const handleAccountOptionKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const selectable = (accounts ?? [])
      .map((account, optionIndex) => (account.active ? -1 : optionIndex))
      .filter(optionIndex => optionIndex >= 0)

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
      suppressTriggerOpen.current = true
      closeSwitcher()
      triggerRef.current?.focus()
    }
  }

  return (
    <div
      className={styles.accountPopup}
      data-side={submenuSide}
      onBlur={handlePopupBlur}
      onPointerLeave={closeSwitcher}
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
        <div className={styles.accountSwitch} onPointerEnter={() => openSwitcher()}>
          <button
            aria-expanded={switchOpen}
            aria-haspopup="menu"
            className={styles.menuAction}
            onClick={() => openSwitcher()}
            onFocus={() => {
              if (suppressTriggerOpen.current) {
                suppressTriggerOpen.current = false
              } else {
                openSwitcher()
              }
            }}
            onKeyDown={handleSwitchKeyDown}
            ref={triggerRef}
            type="button"
          >
            <span>{t.switchAccount}</span>
            <ArrowRight aria-hidden="true" size={14} />
          </button>
        </div>
        {failed && (
          <button
            className={styles.menuAction}
            onClick={() => setRetry(value => value + 1)}
            tabIndex={switchOpen ? -1 : 0}
            type="button"
          >
            {t.retry}
          </button>
        )}
        <button
          className={styles.menuAction}
          disabled={busy}
          onClick={() => void run(() => window.spiritagent.desktop.addAccount())}
          tabIndex={switchOpen ? -1 : 0}
          type="button"
        >
          {t.addAccount}
        </button>
      </div>
      {switchOpen && (
        <div aria-label={t.accountList} className={styles.accountSubmenu} role="menu">
          <div aria-hidden="true" className={styles.accountSubmenuTitle}>
            {t.accountList}
          </div>
          {accounts === null && !failed && <span className={styles.menuStatus}>{t.accountLoading}</span>}
          {failed && <span className={styles.menuStatus}>{t.accountLoadFailed}</span>}
          {accounts?.map((account, index) => (
            <button
              aria-checked={account.active}
              className={styles.accountOption}
              disabled={busy || account.active}
              key={account.id}
              onClick={() => void run(() => window.spiritagent.desktop.switchAccount(account.id))}
              onKeyDown={event => handleAccountOptionKeyDown(event, index)}
              ref={element => {
                accountOptionRefs.current[index] = element
              }}
              role="menuitemradio"
              tabIndex={account.active || index !== accounts.findIndex(item => !item.active) ? -1 : 0}
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
