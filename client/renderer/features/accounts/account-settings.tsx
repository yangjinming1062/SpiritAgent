import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ArrowLeft, Check } from '@/shared/lib/icons'
import { shellApi } from '@/shared/platform/desktop'
import { useStrings } from '@/shared/strings'

import styles from './account-settings.module.css'
import { accountDetail, useAccountAction, useAccounts } from './use-accounts'

export function AccountSettings({ onClose }: { onClose: () => void }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.accounts
  const { accounts, failed, reload } = useAccounts()
  const { busy, run } = useAccountAction()
  const [removing, setRemoving] = useState(false)
  const backRef = useRef<HTMLButtonElement>(null)
  const removeRef = useRef<HTMLButtonElement>(null)
  const previousRemoving = useRef(false)
  const currentAccount = accounts?.find(account => account.active)
  const currentDetail = currentAccount ? accountDetail(currentAccount, accounts) : null

  const act = (action: () => Promise<unknown>): void => {
    void run(action, onClose)
  }

  // 视图切换会卸载当前焦点所在按钮，焦点交给新视图的首个控件。
  useEffect(() => {
    if (removing) {
      backRef.current?.focus()
    } else if (previousRemoving.current) {
      removeRef.current?.focus()
    }

    previousRemoving.current = removing
  }, [removing])

  return (
    <section
      aria-label={t.title}
      className={styles.section}
      onKeyDown={event => {
        if (removing && event.key === 'Escape' && !event.nativeEvent.isComposing) {
          event.preventDefault()
          event.stopPropagation()
          setRemoving(false)
        }
      }}
    >
      <div className={styles.heading}>
        <span>{removing ? t.removableAccounts : t.title}</span>
        {removing && (
          <button
            aria-label={strings.common.back}
            className={styles.back}
            onClick={() => setRemoving(false)}
            ref={backRef}
            type="button"
          >
            <ArrowLeft aria-hidden="true" size={14} />
          </button>
        )}
      </div>
      {accounts === null && !failed && (
        <span className={styles.status} role="status">
          {t.accountLoading}
        </span>
      )}
      {failed && (
        <div className={styles.failure}>
          <span role="status">{t.accountLoadFailed}</span>
          <button onClick={reload} type="button">
            {strings.common.retry}
          </button>
        </div>
      )}
      {currentAccount && !removing && (
        <span className={styles.current}>
          <span>{t.currentAccount}</span>
          <strong>{currentAccount.username}</strong>
          {currentDetail && <small>{currentDetail}</small>}
        </span>
      )}
      {accounts !== null && accounts.length > 0 && (
        <div className={styles.list} role="group">
          {accounts.map(account => {
            const detail = accountDetail(account, accounts)
            let label = `${t.switchAccount} · ${account.username}`

            if (removing) {
              label = `${t.removeAccount} · ${account.username}`
            } else if (account.active) {
              label = `${account.username} · ${t.currentAccount}`
            }

            return (
              <button
                aria-label={label}
                className={styles.row}
                data-active={account.active}
                data-destructive={removing}
                disabled={busy || (!removing && account.active)}
                key={account.id}
                onClick={() =>
                  act(() =>
                    removing
                      ? shellApi().desktop.removeAccount(account.id)
                      : shellApi().desktop.switchAccount(account.id)
                  )
                }
                title={account.baseUrl}
                type="button"
              >
                <span className={styles.rowText}>
                  <span>{account.username}</span>
                  {detail && <small>{detail}</small>}
                </span>
                {account.active && <Check aria-hidden="true" size={14} />}
              </button>
            )
          })}
        </div>
      )}
      {!removing && (
        <div className={styles.actions}>
          <button disabled={busy} onClick={() => act(() => shellApi().desktop.addAccount())} type="button">
            {t.addAccount}
          </button>
          <button
            disabled={busy || accounts === null || accounts.length === 0}
            onClick={() => setRemoving(true)}
            ref={removeRef}
            type="button"
          >
            {t.removeAccount}
          </button>
        </div>
      )}
    </section>
  )
}
