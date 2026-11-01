import { useCallback, useState } from 'react'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { notify, notifyError } from '@/shared/store/notifications'

import { useAsyncGuard } from './use-async-guard'

/**
 * 面板异步动作的统一装配：busy 状态 + 换号/登出与卸载守卫 + 失败 toast。
 *
 * 三条不变语义：换号或登出后不回写、组件卸载后不回写、失败只提示一次。
 * `key` 支持行级 busy（如列表中单行的删除），无 key 时整个面板共用一个 busy 位。
 */
export function usePanelAction() {
  const [busy, setBusy] = useState(false)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const begin = useAsyncGuard()

  const run = useCallback(
    async <T>(
      operation: () => Promise<T>,
      options: { failure: string; success?: string; key?: string } = { failure: '' }
    ): Promise<T | undefined> => {
      const { failure, success, key } = options
      const live = begin()
      const scope = captureAuthScope()

      if (key === undefined) {
        setBusy(true)
      } else {
        setBusyKey(key)
      }

      try {
        const value = await operation()

        if (live() && scope?.() !== false && success !== undefined) {
          notify({ kind: 'success', message: success })
        }

        return value
      } catch (error) {
        if (live() && scope?.() !== false) {
          notifyError(error, failure)
        }

        return undefined
      } finally {
        if (live()) {
          if (key === undefined) {
            setBusy(false)
          } else {
            setBusyKey(current => (current === key ? null : current))
          }
        }
      }
    },
    [begin]
  )

  return { run, busy, busyKey }
}
