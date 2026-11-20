import { captureAuthScope } from '@/shared/lib/authed-api'
import { definePersistedAtom } from '@/shared/lib/storage'

import { isRecord } from '../../../../shared/is-record'

// 失败保留原请求身份，只有发起请求的账户可在成功后清除它。
export function creationSubmitter(storageKey: string) {
  const requests = definePersistedAtom<{ ids: Record<string, string> }>({
    key: storageKey,
    fallback: { ids: {} },
    isPersistable: (value): value is { ids: Record<string, string> } =>
      isRecord(value) && isRecord(value.ids) && Object.values(value.ids).every(id => typeof id === 'string')
  })

  return async <T>(draft: Record<string, unknown>, submit: (requestId: string) => Promise<T>): Promise<T> => {
    const current = captureAuthScope()

    if (!current) {
      throw new Error('请先登录')
    }

    const key = JSON.stringify(draft)
    const ids = requests.get().ids
    const id = ids[key] ?? crypto.randomUUID()

    if (!ids[key]) {
      requests.set({ ids: { ...ids, [key]: id } })
    }

    const result = await submit(id)

    if (current() && requests.get().ids[key] === id) {
      const remaining = { ...requests.get().ids }
      delete remaining[key]
      requests.set({ ids: remaining })
    }

    return result
  }
}
