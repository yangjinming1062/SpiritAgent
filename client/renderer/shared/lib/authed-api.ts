import { $auth } from '@/shared/store/auth'
import type { SpiritAgentApiRequest } from '@ipc/contracts'

import { log } from './log'
import { currentClearEpoch } from './storage'

/** 鉴权 RPC 的统一返回：不把 auth-loss / 网络错 / 后端错 / void body 压成同一个 null，调用方按 reason 分流——避免各 store 把"登出 race"误判成"请求成功无返回"而把状态卡在中间态。 */
export type AuthedApiResult<T> =
  | { ok: true; value: T | null }
  | { ok: false; reason: 'unauth' }
  | { ok: false; reason: 'err'; error: unknown }

/** 捕获发起时的鉴权会话与清理代次；返回的判活函数在二者任一变化（登出、换号）后为 false。未鉴权返回 null。 */
export function captureAuthScope(): (() => boolean) | null {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return null
  }

  const epoch = currentClearEpoch()
  const sessionId = auth.snapshot.sessionId

  return () => {
    const current = $auth.get()

    return current.kind === 'authenticated' && current.snapshot.sessionId === sessionId && currentClearEpoch() === epoch
  }
}

export async function authedApi<T>(opts: SpiritAgentApiRequest): Promise<AuthedApiResult<T>> {
  // 请求结果只属于发起时的鉴权会话和清理代次。
  const auth = $auth.get()
  const isCurrent = captureAuthScope()

  if (!isCurrent || auth.kind !== 'authenticated') {
    return { ok: false, reason: 'unauth' }
  }

  let result: AuthedApiResult<T>

  try {
    result = {
      ok: true,
      value: (await window.spiritagent.api<T>({ ...opts, authSessionId: auth.snapshot.sessionId })) as T | null
    }
  } catch (error) {
    result = { error, ok: false, reason: 'err' }
  }

  return isCurrent() ? result : { ok: false, reason: 'unauth' }
}

/** 收窄到成功结果；真实请求错误记一条警告，登出（unauth）静默。 */
export function apiSucceeded<T>(
  result: AuthedApiResult<T>,
  scope: string,
  message: string
): result is Extract<AuthedApiResult<T>, { ok: true }> {
  if (!result.ok && result.reason === 'err') {
    log.warn(scope, message, result.error)
  }

  return result.ok
}
