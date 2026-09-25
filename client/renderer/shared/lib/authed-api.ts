import type { SpiritAgentApiRequest } from '@ipc/contracts'

import { $auth } from '@/shared/store/auth'

import { currentClearEpoch } from './storage'

/** 鉴权 RPC 的统一返回：不把 auth-loss / 网络错 / 后端错 / void body 压成同一个 null，
 * 调用方按 reason 分流——避免各 store 把"登出 race"误判成"请求成功无返回"而把状态卡在中间态。 */
type AuthedApiResult<T> =
  | { ok: true; value: T | null }
  | { ok: false; reason: 'unauth' }
  | { ok: false; reason: 'err'; error: unknown }

export async function authedApi<T>(opts: SpiritAgentApiRequest): Promise<AuthedApiResult<T>> {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return { ok: false, reason: 'unauth' }
  }

  // 请求结果只属于发起时的鉴权会话和清理代次。
  const epoch = currentClearEpoch()
  const sessionId = auth.snapshot.sessionId

  const isCurrent = (): boolean => {
    const current = $auth.get()

    return current.kind === 'authenticated' && current.snapshot.sessionId === sessionId && currentClearEpoch() === epoch
  }

  try {
    const value = (await window.spiritagent.api<T>(opts)) as T | null

    if (!isCurrent()) {
      return { ok: false, reason: 'unauth' }
    }

    return { ok: true, value }
  } catch (error) {
    if (!isCurrent()) {
      return { ok: false, reason: 'unauth' }
    }

    return { ok: false, error, reason: 'err' }
  }
}
