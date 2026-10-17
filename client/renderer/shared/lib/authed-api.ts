import { $auth } from '@/shared/store/auth'

import { log } from './log'
import { currentClearEpoch } from './storage'

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
