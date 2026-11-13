import { useCallback, useEffect, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

export type Accounts = Awaited<ReturnType<DesktopBridge['desktop']['accounts']>>
type Account = Accounts[number]

// 同一用户名出现在多个后端时补充后端地址，避免账户列表无法区分。
export function accountDetail(account: Account, accounts: Accounts | null): string | null {
  return (accounts ?? []).some(item => item.id !== account.id && item.username === account.username)
    ? account.baseUrl
    : null
}

export function useAccounts({
  onLoadError
}: {
  onLoadError?: (error: unknown) => void
} = {}): {
  accounts: Accounts | null
  failed: boolean
  reload: () => void
} {
  const [accounts, setAccounts] = useState<Accounts | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const onLoadErrorRef = useLatestRef(onLoadError)

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
          onLoadErrorRef.current?.(error)
        }
      })

    return () => {
      disposed = true
    }
  }, [attempt, onLoadErrorRef])

  const reload = useCallback((): void => setAttempt(value => value + 1), [])

  return { accounts, failed, reload }
}

export function useAccountAction(): {
  busy: boolean
  run: (action: () => Promise<unknown>, before?: () => void) => Promise<void>
} {
  const strings = useStrings()
  const [busy, setBusy] = useState(false)
  const beginAsync = useAsyncGuard()

  const run = useCallback(
    (action: () => Promise<unknown>, before?: () => void): Promise<void> => {
      const isCurrent = captureAuthScope()

      if (!isCurrent) {
        return Promise.resolve()
      }

      const isLive = beginAsync()
      setBusy(true)
      before?.()

      return (async () => {
        try {
          await action()
        } catch (error) {
          if (isCurrent()) {
            notifyError(error, strings.common.operationFailed)
          }
        } finally {
          if (isLive()) {
            setBusy(false)
          }
        }
      })()
    },
    [beginAsync, strings.common.operationFailed]
  )

  return { busy, run }
}
