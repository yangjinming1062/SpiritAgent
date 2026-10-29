import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

import { ConfirmDialog } from './confirm-dialog'

export interface ConfirmRequest {
  title: string
  description?: string
  confirmLabel: string
  variant?: 'default' | 'destructive'
}

type PendingConfirm = ConfirmRequest & { resolve: (confirmed: boolean) => void }

const $pendingConfirm = atom<PendingConfirm | null>(null)

function settle(confirmed: boolean, expected = $pendingConfirm.get()): void {
  const pending = $pendingConfirm.get()

  if (pending && pending === expected) {
    $pendingConfirm.set(null)
    pending.resolve(confirmed)
  }
}

// 换号或登出时按取消收尾，不让旧账户的确认结果驱动后续操作。
registerStorageClearHandler(() => settle(false))

/** 命令式二次确认：Promise 在用户选择后兑现；同窗口只展示一个请求，新请求按取消结算未决请求。 */
export function confirmAction(request: ConfirmRequest): Promise<boolean> {
  return new Promise(resolve => {
    settle(false)
    $pendingConfirm.set({ ...request, resolve })
  })
}

/** 确认弹窗宿主：每个窗口根装配一个，与 NotificationStack 同级。 */
export function ConfirmHost(): React.JSX.Element {
  const pending = useStore($pendingConfirm)

  return (
    <ConfirmDialog
      confirmLabel={pending?.confirmLabel ?? ''}
      description={pending?.description}
      onConfirm={() => settle(true, pending)}
      onOpenChange={open => {
        if (!open) {
          settle(false, pending)
        }
      }}
      open={pending !== null}
      title={pending?.title ?? ''}
      variant={pending?.variant}
    />
  )
}
