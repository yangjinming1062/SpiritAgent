import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { ArrowLeft, ArrowRight, Check } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { shellApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

import styles from './account-menu.module.css'

type Accounts = Awaited<ReturnType<DesktopBridge['desktop']['accounts']>>
const ACCOUNT_SUBMENU_WIDTH = 224
const ACCOUNT_SUBMENU_GAP = 8
const VIEWPORT_MARGIN = 12
type AccountSubmenuSide = 'left' | 'overlay' | 'right'
type AccountSubmenu = 'remove' | 'switch'

const zh = {
  accounts: '账户',
  currentAccount: '当前账户',
  switchAccount: '切换账户',
  accountList: '可切换账户',
  accountLoadFailed: '账户读取失败',
  addAccount: '添加账户',
  removeAccount: '移除账户',
  removableAccounts: '选择要移除的账户',
  accountLoading: '正在读取账户…',
  retry: '重试'
}

const en: typeof zh = {
  accounts: 'Accounts',
  currentAccount: 'Current account',
  switchAccount: 'Switch account',
  accountList: 'Available accounts',
  accountLoadFailed: 'Could not read accounts',
  addAccount: 'Add account',
  removeAccount: 'Remove account',
  removableAccounts: 'Choose an account to remove',
  accountLoading: 'Loading accounts…',
  retry: 'Retry'
}

function useAccountMenuStrings(): typeof zh {
  return useStore($locale) === 'en' ? en : zh
}

function selectableAccountIndexes(accounts: Accounts | null, submenu: AccountSubmenu): number[] {
  return (accounts ?? []).flatMap((account, index) => (submenu === 'switch' && account.active ? [] : [index]))
}

function accountSubmenuSide(left: number, right: number): AccountSubmenuSide {
  if (window.innerWidth - right - VIEWPORT_MARGIN >= ACCOUNT_SUBMENU_WIDTH + ACCOUNT_SUBMENU_GAP) {
    return 'right'
  }

  return left - VIEWPORT_MARGIN >= ACCOUNT_SUBMENU_WIDTH + ACCOUNT_SUBMENU_GAP ? 'left' : 'overlay'
}

export function AccountMenu({
  onClose,
  placement = 'below'
}: {
  onClose: () => void
  placement?: 'above' | 'below' | 'top-center'
}): React.JSX.Element {
  const t = useAccountMenuStrings()
  const [accounts, setAccounts] = useState<Accounts | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [submenu, setSubmenu] = useState<AccountSubmenu | null>(null)
  const [submenuSide, setSubmenuSide] = useState<AccountSubmenuSide>('right')
  const beginAsync = useAsyncGuard()
  const menuRef = useRef<HTMLDivElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const submenuRef = useRef<HTMLDivElement>(null)
  const submenuContentRef = useRef<HTMLDivElement>(null)
  const submenuBackRef = useRef<HTMLButtonElement>(null)
  const triggerRefs = useRef<Record<AccountSubmenu, HTMLButtonElement | null>>({ remove: null, switch: null })
  const accountOptionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const focusOptionOnOpen = useRef(false)
  const focusDirectionOnOpen = useRef<'first' | 'last'>('first')
  const suppressTriggerOpen = useRef<AccountSubmenu | null>(null)
  useInteractiveRegion('account-menu-popup', popupRef)
  useInteractiveRegion('account-menu-submenu', submenuRef)

  useLayoutEffect(() => {
    const popup = popupRef.current
    const menu = menuRef.current

    if (!popup || !menu) {
      return
    }

    const updatePosition = (): void => {
      popup.style.setProperty('--account-offset-x', '0px')
      popup.style.setProperty('--account-offset-y', '0px')

      const rect = menu.getBoundingClientRect()
      const left = Math.max(VIEWPORT_MARGIN, Math.min(rect.left, window.innerWidth - rect.width - VIEWPORT_MARGIN))
      const top = Math.max(VIEWPORT_MARGIN, Math.min(rect.top, window.innerHeight - rect.height - VIEWPORT_MARGIN))
      popup.style.setProperty('--account-offset-x', `${left - rect.left}px`)
      popup.style.setProperty('--account-offset-y', `${top - rect.top}px`)
      popup.style.setProperty('--account-menu-left', `${menu.offsetLeft}px`)
      popup.style.setProperty('--account-menu-top', `${menu.offsetTop}px`)
      popup.style.setProperty('--account-menu-width', `${menu.offsetWidth}px`)
      popup.style.setProperty('--account-menu-height', `${menu.offsetHeight}px`)

      if (!submenu) {
        return
      }

      const side = accountSubmenuSide(left, left + rect.width)
      const trigger = triggerRefs.current[submenu]
      setSubmenuSide(side)
      popup.style.setProperty(
        '--account-submenu-top',
        `${menu.offsetTop + (trigger ? trigger.getBoundingClientRect().top - top : 0)}px`
      )
      popup.style.setProperty('--account-submenu-height', `${window.innerHeight - VIEWPORT_MARGIN * 2}px`)

      const submenuElement = submenuRef.current

      if (submenuElement) {
        submenuElement.style.setProperty('--account-submenu-offset-y', '0px')
        // 外层包含连接间隙，鼠标跨入二级菜单时继续保持捕获。
        submenuElement.dataset.side = side

        const submenuRect = submenuElement.getBoundingClientRect()

        const submenuTop = Math.max(
          VIEWPORT_MARGIN,
          Math.min(submenuRect.top, window.innerHeight - submenuRect.height - VIEWPORT_MARGIN)
        )

        submenuElement.style.setProperty('--account-submenu-offset-y', `${submenuTop - submenuRect.top}px`)
      }
    }

    updatePosition()
    const observer = new ResizeObserver(updatePosition)
    observer.observe(menu)

    if (submenuRef.current?.firstElementChild) {
      observer.observe(submenuRef.current.firstElementChild)
    }

    window.addEventListener('resize', updatePosition)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updatePosition)
    }
  }, [placement, submenu])

  const closeSubmenu = (): void => {
    focusOptionOnOpen.current = false
    focusDirectionOnOpen.current = 'first'
    setSubmenu(null)
  }

  const returnToSubmenuTrigger = (): void => {
    if (!submenu) {
      return
    }

    suppressTriggerOpen.current = submenu
    closeSubmenu()
    triggerRefs.current[submenu]?.focus()
  }

  const focusAccountOption = useCallback(
    (mode: AccountSubmenu, direction: 'first' | 'last' = 'first'): boolean => {
      const selectable = selectableAccountIndexes(accounts, mode)

      const index = direction === 'last' ? selectable.at(-1) : selectable[0]

      const target =
        (index === undefined ? null : accountOptionRefs.current[index]) ??
        submenuBackRef.current ??
        submenuContentRef.current

      target?.focus()

      return Boolean(target && document.activeElement === target)
    },
    [accounts]
  )

  const openSubmenu = (mode: AccountSubmenu, focusOption = false, direction: 'first' | 'last' = 'first'): void => {
    if (busy) {
      return
    }

    if (focusOption) {
      if (submenu === mode) {
        focusOptionOnOpen.current = !focusAccountOption(mode, direction)
      } else {
        focusOptionOnOpen.current = true
        focusDirectionOnOpen.current = direction
      }
    } else {
      focusOptionOnOpen.current = false
    }

    setSubmenu(mode)
  }

  const previewSubmenu = (mode: AccountSubmenu): void => {
    const rect = menuRef.current?.getBoundingClientRect()

    // 覆盖菜单在点击完成或键盘激活后打开，避免覆盖层接走触发按钮的点击。
    if (rect && accountSubmenuSide(rect.left, rect.right) !== 'overlay') {
      openSubmenu(mode)
    }
  }

  useEffect(() => {
    if (!submenu) {
      return
    }

    const overlayNeedsFocus =
      submenuSide === 'overlay' &&
      (menuRef.current?.contains(document.activeElement) || document.activeElement === submenuContentRef.current)

    if (!focusOptionOnOpen.current && !overlayNeedsFocus) {
      return
    }

    focusOptionOnOpen.current = !focusAccountOption(submenu, focusDirectionOnOpen.current)
  }, [focusAccountOption, submenu, submenuSide])

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
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    const isLive = beginAsync()
    setBusy(true)
    closeSubmenu()
    onClose()

    try {
      await action()
    } catch (error) {
      if (isCurrent()) {
        notifyError(error, t.accounts)
      }
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
      returnToSubmenuTrigger()
    }
  }

  const submenuTrigger = (mode: AccountSubmenu, label: string): React.JSX.Element => (
    <div className={styles.accountSwitch} onPointerEnter={() => previewSubmenu(mode)}>
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
            previewSubmenu(mode)
          }
        }}
        onKeyDown={event => handleTriggerKeyDown(event, mode)}
        ref={element => {
          triggerRefs.current[mode] = element
        }}
        tabIndex={submenu && submenuSide === 'overlay' ? -1 : 0}
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
      data-placement={placement}
      data-side={submenuSide}
      onBlur={handlePopupBlur}
      onKeyDown={event => {
        if (event.key !== 'Escape') {
          return
        }

        event.preventDefault()
        event.stopPropagation()

        if (submenu) {
          returnToSubmenuTrigger()
        } else {
          onClose()
        }
      }}
      onPointerLeave={() => {
        if (!submenuRef.current?.contains(document.activeElement)) {
          closeSubmenu()
        }
      }}
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
        <div className={styles.accountSubmenuAnchor} data-side={submenuSide} ref={submenuRef}>
          <div
            aria-label={submenuLabel}
            className={styles.accountSubmenu}
            ref={submenuContentRef}
            role="menu"
            tabIndex={-1}
          >
            {submenuSide === 'overlay' ? (
              <button
                className={styles.accountSubmenuBack}
                onClick={returnToSubmenuTrigger}
                ref={submenuBackRef}
                role="menuitem"
                type="button"
              >
                <ArrowLeft aria-hidden="true" size={14} />
                <span>{submenuLabel}</span>
              </button>
            ) : (
              <div aria-hidden="true" className={styles.accountSubmenuTitle}>
                {submenuLabel}
              </div>
            )}
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
        </div>
      )}
    </div>
  )
}
